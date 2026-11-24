import com.comsol.model.*;
import com.comsol.model.physics.*;
import com.comsol.model.util.*;
import java.io.File;
import java.io.StringReader;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 3D ROV Ducted Propeller COMSOL Java API Bridge.
 *
 * Uses explicit static named nested classes only (zero anonymous inner classes or lambdas)
 * so compiled bytecode names remain deterministic across javac and comsolcompile.
 */
public final class PropellerBridge {

    private static final String[] SPEC_KEYS = {
        "speed_rpm",
        "cad_path",
        "cad_type",
        "rebuild_geometry",
        "solve",
        "mesh_level",
        "export_surface_dir",
        "warm_start",
        "initsol",
        "stol",
        "segcflaa",
        "submincfl",
        "subdamp",
        "maxiter",
        "save_output",
        "strict_convergence"
    };

    private static final class StrictProperties extends Properties {
        @Override
        public synchronized Object put(Object key, Object value) {
            if (containsKey(key)) {
                throw new IllegalArgumentException("Duplicate property: " + key);
            }
            return super.put(key, value);
        }
    }

    static final class EvalSpec {
        final boolean autoSpeed;
        final double speedRpm;
        final String cadPath;
        final String cadType;
        final boolean rebuildGeometry;
        final boolean solve;
        final String meshLevel;
        final String exportSurfaceDir;
        final String warmStart;
        final String initsol;
        final double stol;
        final String segcflaa;
        final double submincfl;
        final double subdamp;
        final int maxiter;
        final boolean saveOutput;
        final boolean strictConvergence;
        final Map<String, String> extraParams;

        EvalSpec(String argument, boolean allowAutoSpeed) throws Exception {
            if (argument == null || !argument.startsWith("base64:")) {
                throw new IllegalArgumentException("Properties argument must start with base64:");
            }
            byte[] bytes = Base64.getDecoder().decode(argument.substring(7));
            if (bytes.length == 0 || bytes.length > 65536) {
                throw new IllegalArgumentException("Properties payload must contain 1..65536 bytes");
            }
            String text = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString();
            Properties props = new StrictProperties();
            props.load(new StringReader(text));

            Set<String> allowed = new HashSet<>(Arrays.asList(SPEC_KEYS));
            Map<String, String> extras = new LinkedHashMap<>();
            for (String key : props.stringPropertyNames()) {
                if (key.startsWith("param.")) {
                    String pname = key.substring(6).trim();
                    if (pname.isEmpty() || !pname.matches("[A-Za-z_][A-Za-z0-9_]*")) {
                        throw new IllegalArgumentException("Invalid extra parameter name: " + pname);
                    }
                    String pexpr = props.getProperty(key);
                    if (pexpr == null || pexpr.trim().isEmpty()) {
                        throw new IllegalArgumentException("Empty expression for parameter: " + pname);
                    }
                    extras.put(pname, pexpr.trim());
                } else if (!allowed.contains(key)) {
                    throw new IllegalArgumentException("Unknown property key: " + key);
                }
            }
            this.extraParams = extras;

            String speedRaw = props.getProperty("speed_rpm", "").trim();
            if (speedRaw.isEmpty()) {
                throw new IllegalArgumentException("Missing required property: speed_rpm");
            }
            if ("auto".equalsIgnoreCase(speedRaw)) {
                if (!allowAutoSpeed) {
                    throw new IllegalArgumentException("speed_rpm=auto is only permitted for baseline reproduction");
                }
                this.autoSpeed = true;
                this.speedRpm = 0.0;
            } else {
                this.autoSpeed = false;
                double rpm = parseFiniteDouble(speedRaw, "speed_rpm");
                if (rpm == 0.0) {
                    throw new IllegalArgumentException("speed_rpm must be nonzero");
                }
                this.speedRpm = rpm;
            }

            this.cadPath = props.getProperty("cad_path", "").trim();
            this.cadType = props.getProperty("cad_type", "PARASOLID").trim();
            if (this.cadType.isEmpty()) {
                throw new IllegalArgumentException("cad_type cannot be empty");
            }
            this.rebuildGeometry = parseBooleanStrict(
                props.getProperty("rebuild_geometry", "false").trim(), "rebuild_geometry"
            );
            this.solve = parseBooleanStrict(
                props.getProperty("solve", "true").trim(), "solve"
            );
            this.meshLevel = props.getProperty("mesh_level", "fine").trim().toLowerCase(Locale.ROOT);
            this.exportSurfaceDir = props.getProperty("export_surface_dir", "").trim();

            String ws = props.getProperty("warm_start", "auto").trim().toLowerCase(Locale.ROOT);
            if (!("auto".equals(ws) || "seed".equals(ws) || "init".equals(ws))) {
                throw new IllegalArgumentException("warm_start must be auto, seed, or init");
            }
            this.warmStart = ws;

            this.initsol = props.getProperty("initsol", "seed").trim();
            if (this.initsol.isEmpty()) {
                throw new IllegalArgumentException("initsol cannot be empty");
            }

            this.stol = parseFiniteDouble(props.getProperty("stol", "0.0025").trim(), "stol");
            if (this.stol <= 0.0) {
                throw new IllegalArgumentException("stol must be positive");
            }

            String cflaa = props.getProperty("segcflaa", "off").trim().toLowerCase(Locale.ROOT);
            if (!("on".equals(cflaa) || "off".equals(cflaa))) {
                throw new IllegalArgumentException("segcflaa must be on or off");
            }
            this.segcflaa = cflaa;

            this.submincfl = parseFiniteDouble(props.getProperty("submincfl", "500.0").trim(), "submincfl");
            if (this.submincfl <= 0.0) {
                throw new IllegalArgumentException("submincfl must be positive");
            }

            this.subdamp = parseFiniteDouble(props.getProperty("subdamp", "0.35").trim(), "subdamp");
            if (this.subdamp <= 0.0 || this.subdamp > 1.0) {
                throw new IllegalArgumentException("subdamp must lie in (0, 1]");
            }

            String maxiterStr = props.getProperty("maxiter", "100").trim();
            try {
                this.maxiter = Integer.parseInt(maxiterStr);
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException("Invalid maxiter integer: " + maxiterStr);
            }
            if (this.maxiter < 1) {
                throw new IllegalArgumentException("maxiter must be >= 1");
            }

            this.saveOutput = parseBooleanStrict(props.getProperty("save_output", "false").trim(), "save_output");
            this.strictConvergence = parseBooleanStrict(
                props.getProperty("strict_convergence", "true").trim(), "strict_convergence"
            );
        }
    }

    private PropellerBridge() {}

    private static double parseFiniteDouble(String raw, String name) {
        double v;
        try {
            v = Double.parseDouble(raw);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Invalid number for " + name + ": " + raw);
        }
        if (!Double.isFinite(v)) {
            throw new IllegalArgumentException("Non-finite number for " + name + ": " + raw);
        }
        return v;
    }

    private static boolean parseBooleanStrict(String raw, String name) {
        if ("true".equalsIgnoreCase(raw)) return true;
        if ("false".equalsIgnoreCase(raw)) return false;
        throw new IllegalArgumentException("Invalid boolean for " + name + ": " + raw);
    }

    private static boolean containsTag(String[] tags, String target) {
        if (tags == null) return false;
        for (String t : tags) {
            if (target.equals(t)) return true;
        }
        return false;
    }

    private static void safeRemoveDataset(Model model, String tag) {
        try {
            if (containsTag(model.result().dataset().tags(), tag)) {
                model.result().dataset().remove(tag);
            }
        } catch (Throwable ignored) {}
    }

    private static void cleanupTempNodes(Model model) {
        String[] numTags;
        try {
            numTags = model.result().numerical().tags();
        } catch (Throwable t) {
            numTags = new String[0];
        }
        for (String tag : numTags) {
            if (tag.startsWith("tmp_prop_")) {
                ComsolBridge.safeRemoveNumerical(model, tag);
            }
        }
        String[] expTags;
        try {
            expTags = model.result().export().tags();
        } catch (Throwable t) {
            expTags = new String[0];
        }
        for (String tag : expTags) {
            if (tag.startsWith("tmp_prop_")) {
                try { model.result().export().remove(tag); } catch (Throwable ignored) {}
            }
        }
        String[] dsTags;
        try {
            dsTags = model.result().dataset().tags();
        } catch (Throwable t) {
            dsTags = new String[0];
        }
        for (String tag : dsTags) {
            if (tag.startsWith("tmp_prop_")) {
                safeRemoveDataset(model, tag);
            }
        }
    }

    private static String ensureEvalDataset(Model model, String compTag, String meshTag, String solTag, boolean forceMesh) {
        if (!forceMesh && containsTag(model.result().dataset().tags(), "dset1")) {
            try {
                if (containsTag(model.sol().tags(), solTag) && !model.sol(solTag).isEmpty()) {
                    return "dset1";
                }
            } catch (Throwable ignored) {}
        }
        safeRemoveDataset(model, "tmp_prop_mesh_ds");
        DatasetFeature ds = model.result().dataset().create("tmp_prop_mesh_ds", "Mesh");
        try {
            ds.set("mesh", meshTag);
        } catch (Throwable ignored) {}
        return "tmp_prop_mesh_ds";
    }

    private static double[] evalSurfaceMinMax(Model model, String dsetTag, String selTag, int[] entities, String[] exprs) {
        String maxTag = "tmp_prop_smax";
        String minTag = "tmp_prop_smin";
        ComsolBridge.safeRemoveNumerical(model, maxTag);
        ComsolBridge.safeRemoveNumerical(model, minTag);
        try {
            NumericalFeature fmax = model.result().numerical().create(maxTag, "MaxSurface");
            fmax.set("data", dsetTag);
            if (selTag != null) {
                fmax.selection().named(selTag);
            } else {
                fmax.selection().set(entities);
            }
            fmax.set("expr", exprs);
            double[][] vmax = fmax.getReal();

            NumericalFeature fmin = model.result().numerical().create(minTag, "MinSurface");
            fmin.set("data", dsetTag);
            if (selTag != null) {
                fmin.selection().named(selTag);
            } else {
                fmin.selection().set(entities);
            }
            fmin.set("expr", exprs);
            double[][] vmin = fmin.getReal();

            double[] out = new double[exprs.length * 2];
            for (int i = 0; i < exprs.length; i++) {
                out[2 * i] = vmin[i][0];
                out[2 * i + 1] = vmax[i][0];
            }
            return out;
        } finally {
            ComsolBridge.safeRemoveNumerical(model, maxTag);
            ComsolBridge.safeRemoveNumerical(model, minTag);
        }
    }

    private static double[] evalVolumeMinMax(Model model, String dsetTag, String selTag, String[] exprs) {
        String maxTag = "tmp_prop_vmax";
        String minTag = "tmp_prop_vmin";
        ComsolBridge.safeRemoveNumerical(model, maxTag);
        ComsolBridge.safeRemoveNumerical(model, minTag);
        try {
            NumericalFeature fmax = model.result().numerical().create(maxTag, "MaxVolume");
            fmax.set("data", dsetTag);
            fmax.selection().named(selTag);
            fmax.set("expr", exprs);
            double[][] vmax = fmax.getReal();

            NumericalFeature fmin = model.result().numerical().create(minTag, "MinVolume");
            fmin.set("data", dsetTag);
            fmin.selection().named(selTag);
            fmin.set("expr", exprs);
            double[][] vmin = fmin.getReal();

            double[] out = new double[exprs.length * 2];
            for (int i = 0; i < exprs.length; i++) {
                out[2 * i] = vmin[i][0];
                out[2 * i + 1] = vmax[i][0];
            }
            return out;
        } finally {
            ComsolBridge.safeRemoveNumerical(model, maxTag);
            ComsolBridge.safeRemoveNumerical(model, minTag);
        }
    }

    private static String boundsJson(double[] mm) {
        // mm = [x_min, x_max, y_min, y_max, z_min, z_max, r_min, r_max]
        return "{\"x_min_m\":" + mm[0] + ",\"x_max_m\":" + mm[1]
            + ",\"y_min_m\":" + mm[2] + ",\"y_max_m\":" + mm[3]
            + ",\"z_min_m\":" + mm[4] + ",\"z_max_m\":" + mm[5]
            + ",\"r_min_m\":" + mm[6] + ",\"r_max_m\":" + mm[7]
            + ",\"d_max_m\":" + (2.0 * mm[7]) + "}";
    }

    private static int[][] classifyFarBoundaries(Model model, ModelNode comp, String dsetTag) {
        if (!containsTag(comp.selection().tags(), "far")) {
            throw new IllegalStateException("Component lacks selection 'far'");
        }
        int[] farEntities = comp.selection("far").entities().clone();
        Arrays.sort(farEntities);
        if (farEntities.length < 3) {
            throw new IllegalStateException("Selection 'far' must contain at least 3 faces, got " + Arrays.toString(farEntities));
        }

        double globalXMin = Double.POSITIVE_INFINITY;
        double globalXMax = Double.NEGATIVE_INFINITY;
        double[][] faceBounds = new double[farEntities.length][];
        String[] exprs = {"x", "y", "z", "sqrt(y^2+z^2)"};
        for (int i = 0; i < farEntities.length; i++) {
            double[] mm = evalSurfaceMinMax(model, dsetTag, null, new int[]{farEntities[i]}, exprs);
            faceBounds[i] = mm;
            globalXMin = Math.min(globalXMin, mm[0]);
            globalXMax = Math.max(globalXMax, mm[1]);
        }

        double tol = 1e-4 * Math.max(1e-6, globalXMax - globalXMin);
        List<Integer> negCap = new ArrayList<>();
        List<Integer> posCap = new ArrayList<>();
        List<Integer> lateral = new ArrayList<>();
        for (int i = 0; i < farEntities.length; i++) {
            double xmin = faceBounds[i][0];
            double xmax = faceBounds[i][1];
            if (Math.abs(xmin - globalXMin) <= tol && Math.abs(xmax - globalXMin) <= tol) {
                negCap.add(farEntities[i]);
            } else if (Math.abs(xmin - globalXMax) <= tol && Math.abs(xmax - globalXMax) <= tol) {
                posCap.add(farEntities[i]);
            } else {
                lateral.add(farEntities[i]);
            }
        }
        if (negCap.isEmpty() || posCap.isEmpty() || lateral.isEmpty()) {
            throw new IllegalStateException(
                "Failed to classify far boundaries: negCap=" + negCap + " posCap=" + posCap + " lateral=" + lateral
            );
        }
        return new int[][]{toSortedIntArray(negCap), toSortedIntArray(posCap), toSortedIntArray(lateral)};
    }

    private static int[] toSortedIntArray(List<Integer> items) {
        int[] arr = new int[items.size()];
        for (int i = 0; i < items.size(); i++) {
            arr[i] = items.get(i);
        }
        Arrays.sort(arr);
        return arr;
    }

    private static int[] unionSorted(int[] a, int[] b) {
        Set<Integer> set = new TreeSet<>();
        for (int v : a) set.add(v);
        for (int v : b) set.add(v);
        int[] out = new int[set.size()];
        int idx = 0;
        for (Integer v : set) {
            out[idx++] = v;
        }
        return out;
    }

    private static int[] sortedCopy(int[] a) {
        if (a == null) return new int[0];
        int[] copy = a.clone();
        Arrays.sort(copy);
        return copy;
    }

    private static String readSolverProp(SolverFeature feat, String prop) {
        try {
            String v = feat.getString(prop);
            if (v != null) return v;
        } catch (Throwable ignored) {}
        try {
            return Double.toString(feat.getDouble(prop));
        } catch (Throwable ignored) {}
        try {
            return Integer.toString(feat.getInt(prop));
        } catch (Throwable ignored) {}
        try {
            return Arrays.toString(feat.getStringArray(prop));
        } catch (Throwable ignored) {}
        return "";
    }

    private static double[] parseLastSolverErrors(String message) {
        if (message == null || message.isEmpty()) return new double[0];
        String[] lines = message.split("\\r?\\n");
        Pattern numPattern = Pattern.compile("[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?");
        double[] lastErrors = new double[0];
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            String lower = line.toLowerCase(Locale.ROOT);
            if (line.contains("分离组的解误差估计") || lower.contains("solution error")) {
                String candidate = "";
                int colonIdx = line.indexOf(':');
                if (colonIdx < 0) colonIdx = line.indexOf('：');
                if (colonIdx >= 0 && colonIdx + 1 < line.length()) {
                    candidate = line.substring(colonIdx + 1).trim();
                }
                if (candidate.isEmpty() && i + 1 < lines.length) {
                    candidate = lines[i + 1].trim();
                }
                List<Double> vals = new ArrayList<>();
                Matcher m = numPattern.matcher(candidate);
                while (m.find()) {
                    try {
                        double v = Double.parseDouble(m.group());
                        if (Double.isFinite(v)) vals.add(v);
                    } catch (NumberFormatException ignored) {}
                }
                if (!vals.isEmpty()) {
                    lastErrors = new double[vals.size()];
                    for (int j = 0; j < vals.size(); j++) {
                        lastErrors[j] = vals.get(j);
                    }
                }
            }
        }
        return lastErrors;
    }

    private static int parseSolverIterations(String message) {
        if (message == null || message.isEmpty()) return -1;
        // Look for the last iteration count line or "Number of iterations" / "迭代次数"
        Pattern p = Pattern.compile("(?m)^\\s*(\\d+)\\s+(?:\\d+\\.\\d+|\\d+(?:\\.\\d+)?[eE][-+]?\\d+)");
        Matcher m = p.matcher(message);
        int lastIter = -1;
        while (m.find()) {
            try {
                lastIter = Integer.parseInt(m.group(1));
            } catch (NumberFormatException ignored) {}
        }
        return lastIter;
    }

    private static String buildGeometryAuditJson(Model model, ModelNode comp, String dsetTag) {
        String[] exprs = {"x", "y", "z", "sqrt(y^2+z^2)"};
        double[] impMm = evalSurfaceMinMax(model, dsetTag, "impeller", null, exprs);
        double[] nozMm = evalSurfaceMinMax(model, dsetTag, "nozzle", null, exprs);

        double dPropM = 2.0 * impMm[7];
        double ductOuterMaxDM = 2.0 * nozMm[7];

        // Estimate hub bounds from impeller boundary faces whose r_max is near 0.042 m
        // and whose axial extent is cylindrical/hub-like (falls back cleanly if absent).
        double hubRMax = Double.NaN;
        double hubXMin = Double.POSITIVE_INFINITY;
        double hubXMax = Double.NEGATIVE_INFINITY;
        int[] impEntities = comp.selection("impeller").entities();
        for (int ent : impEntities) {
            double[] fmm = evalSurfaceMinMax(model, dsetTag, null, new int[]{ent}, exprs);
            if (fmm[7] <= 0.050 && fmm[7] >= 0.035) {
                hubRMax = Double.isNaN(hubRMax) ? fmm[7] : Math.max(hubRMax, fmm[7]);
                hubXMin = Math.min(hubXMin, fmm[0]);
                hubXMax = Math.max(hubXMax, fmm[1]);
            }
        }

        double[] movingMm = null;
        if (containsTag(comp.selection().tags(), "moving")) {
            try {
                movingMm = evalVolumeMinMax(model, dsetTag, "moving", exprs);
            } catch (Throwable ignored) {}
        }

        StringBuilder sb = new StringBuilder("{");
        sb.append("\"d_prop_m\":").append(dPropM);
        sb.append(",\"r_prop_max_m\":").append(impMm[7]);
        sb.append(",\"impeller_bounds\":").append(boundsJson(impMm));
        sb.append(",\"duct_outer_max_d_m\":").append(ductOuterMaxDM);
        sb.append(",\"duct_r_max_m\":").append(nozMm[7]);
        sb.append(",\"nozzle_bounds\":").append(boundsJson(nozMm));
        if (!Double.isNaN(hubRMax)) {
            sb.append(",\"d_hub_m\":").append(2.0 * hubRMax);
            sb.append(",\"hub_r_max_m\":").append(hubRMax);
            sb.append(",\"hub_x_min_m\":").append(hubXMin);
            sb.append(",\"hub_x_max_m\":").append(hubXMax);
        } else {
            sb.append(",\"d_hub_m\":null,\"hub_r_max_m\":null,\"hub_x_min_m\":null,\"hub_x_max_m\":null");
        }
        sb.append(",\"r_prop_m\":").append(impMm[7]);
        sb.append(",\"duct_x_min_m\":").append(nozMm[0]);
        sb.append(",\"duct_x_max_m\":").append(nozMm[1]);
        sb.append(",\"duct_x_span\":[").append(nozMm[0]).append(",").append(nozMm[1]).append("]");
        sb.append(",\"duct_r_min_m\":").append(nozMm[6]);
        sb.append(",\"blade_x_min_m\":").append(impMm[0]);
        sb.append(",\"blade_x_max_m\":").append(impMm[1]);
        sb.append(",\"blade_count\":3");
        double tipGapMeas = nozMm[6] - impMm[7];
        if (Double.isFinite(tipGapMeas)) {
            sb.append(",\"measured_tip_clearance_m\":").append(tipGapMeas);
        }
        if (movingMm != null) {
            sb.append(",\"moving_domain_bounds\":").append(boundsJson(movingMm));
        } else {
            sb.append(",\"moving_domain_bounds\":null");
        }
        sb.append("}");
        return sb.toString();
    }

    private static double readParamWithUnitFactor(Model model, String name, String targetUnit, double fallback) {
        try {
            String[] existing = model.param().varnames();
            if (!containsTag(existing, name)) {
                return fallback;
            }
            if ("deg".equals(targetUnit)) {
                String unit = "";
                try { unit = model.param().evaluateUnit(name); } catch (Throwable ignored) {}
                double val = model.param().evaluate(name);
                if (!Double.isFinite(val)) return fallback;
                if ("rad".equals(unit) || "deg".equals(unit)) {
                    return Math.toDegrees(val);
                }
                return val;
            }
            double val = model.param().evaluate(name);
            return Double.isFinite(val) ? val : fallback;
        } catch (Throwable ignored) {
            return fallback;
        }
    }

    private static double interpQuadSection(double eta, double v04, double v07, double v10) {
        double l0 = ((eta - 0.7) * (eta - 1.0)) / ((0.4 - 0.7) * (0.4 - 1.0));
        double l1 = ((eta - 0.4) * (eta - 1.0)) / ((0.7 - 0.4) * (0.7 - 1.0));
        double l2 = ((eta - 0.4) * (eta - 0.7)) / ((1.0 - 0.4) * (1.0 - 0.7));
        return l0 * v04 + l1 * v07 + l2 * v10;
    }

    private static void clearStaleSolutions(Model model) {
        for (String stag : new String[]{"sol1", "seed"}) {
            try {
                if (containsTag(model.sol().tags(), stag) && !model.sol(stag).isEmpty()) {
                    model.sol(stag).clearSolutionData();
                }
            } catch (Throwable ignored) {}
        }
    }

    private static void safeSetSel(Object selObj, int[] ents) {
        if (selObj == null || ents == null) return;
        try {
            String nm = (String) selObj.getClass().getMethod("named").invoke(selObj);
            if (nm != null && !nm.isEmpty()) {
                return;
            }
        } catch (Throwable ignored) {}
        try {
            selObj.getClass().getMethod("set", int[].class).invoke(selObj, ents);
        } catch (Throwable ignored) {}
    }

    private static double[] rebuildParametricGeometry(Model model, ModelNode comp, GeomSequence g) {
        double pitch04 = readParamWithUnitFactor(model, "pitch_r04_deg", "deg", 28.0);
        double pitch07 = readParamWithUnitFactor(model, "pitch_r07_deg", "deg", 22.0);
        double pitch10 = readParamWithUnitFactor(model, "pitch_r10_deg", "deg", 17.5);
        double chord04 = readParamWithUnitFactor(model, "chord_r04_m", "m", 0.058);
        double chord07 = readParamWithUnitFactor(model, "chord_r07_m", "m", 0.072);
        double chord10 = readParamWithUnitFactor(model, "chord_r10_m", "m", 0.048);
        double camber = readParamWithUnitFactor(model, "max_camber_ratio", "", 0.012);
        double thick = readParamWithUnitFactor(model, "max_thickness_ratio", "", 0.085);
        double skewTip = readParamWithUnitFactor(model, "skew_tip_deg", "deg", 14.0);
        double rakeTip = readParamWithUnitFactor(model, "rake_tip_m", "m", 0.004);
        double tipClear = readParamWithUnitFactor(model, "tip_clearance_m", "m", 0.003);
        double lipRatio = readParamWithUnitFactor(model, "duct_inlet_lip_ratio", "", 1.12);
        double diffAngle = readParamWithUnitFactor(model, "duct_diffuser_angle_deg", "deg", 6.5);

        double rProp = 0.160;
        double rHub = 0.042;
        double rThroat = rProp + tipClear;
        double rRotorflow = rProp + 0.5 * tipClear;

        // Ensure frozen invariants and 13 parameters are explicitly recorded in model.param()
        model.param().set("D_prop", "0.320[m]", "Frozen propeller diameter");
        model.param().set("R_prop", "0.160[m]", "Frozen propeller radius");
        model.param().set("D_hub", "0.084[m]", "Frozen hub diameter");
        if (!containsTag(model.param().varnames(), "pitch_r04_deg")) model.param().set("pitch_r04_deg", pitch04 + "[deg]");
        if (!containsTag(model.param().varnames(), "pitch_r07_deg")) model.param().set("pitch_r07_deg", pitch07 + "[deg]");
        if (!containsTag(model.param().varnames(), "pitch_r10_deg")) model.param().set("pitch_r10_deg", pitch10 + "[deg]");
        if (!containsTag(model.param().varnames(), "chord_r04_m")) model.param().set("chord_r04_m", chord04 + "[m]");
        if (!containsTag(model.param().varnames(), "chord_r07_m")) model.param().set("chord_r07_m", chord07 + "[m]");
        if (!containsTag(model.param().varnames(), "chord_r10_m")) model.param().set("chord_r10_m", chord10 + "[m]");
        if (!containsTag(model.param().varnames(), "max_camber_ratio")) model.param().set("max_camber_ratio", Double.toString(camber));
        if (!containsTag(model.param().varnames(), "max_thickness_ratio")) model.param().set("max_thickness_ratio", Double.toString(thick));
        if (!containsTag(model.param().varnames(), "skew_tip_deg")) model.param().set("skew_tip_deg", skewTip + "[deg]");
        if (!containsTag(model.param().varnames(), "rake_tip_m")) model.param().set("rake_tip_m", rakeTip + "[m]");
        if (!containsTag(model.param().varnames(), "tip_clearance_m")) model.param().set("tip_clearance_m", tipClear + "[m]");
        if (!containsTag(model.param().varnames(), "duct_inlet_lip_ratio")) model.param().set("duct_inlet_lip_ratio", Double.toString(lipRatio));
        if (!containsTag(model.param().varnames(), "duct_diffuser_angle_deg")) model.param().set("duct_diffuser_angle_deg", diffAngle + "[deg]");

        String[] oldTags = g.feature().tags();
        for (int i = oldTags.length - 1; i >= 0; i--) {
            if (!"fin".equals(oldTags[i])) {
                g.feature().remove(oldTags[i]);
            }
        }

        GeomFeature farCyl = g.feature().create("far_cyl", "Cylinder");
        farCyl.set("axistype", "cartesian");
        farCyl.set("axis", new double[]{1, 0, 0});
        farCyl.set("pos", new double[]{-3.390034, 0, 0});
        farCyl.set("r", 1.7);
        farCyl.set("h", 4.932640);

        GeomFeature rotCyl = g.feature().create("rot_cyl", "Cylinder");
        rotCyl.set("axistype", "cartesian");
        rotCyl.set("axis", new double[]{1, 0, 0});
        rotCyl.set("pos", new double[]{0.055, 0, 0});
        rotCyl.set("r", rRotorflow);
        rotCyl.set("h", 0.085);

        GeomFeature hubCyl = g.feature().create("hub_cyl", "Cylinder");
        hubCyl.set("axistype", "cartesian");
        hubCyl.set("axis", new double[]{1, 0, 0});
        hubCyl.set("pos", new double[]{0.065, 0, 0});
        hubCyl.set("r", rHub);
        hubCyl.set("h", 0.065);

        GeomFeature tipCyl = g.feature().create("tip_cyl", "Cylinder");
        tipCyl.set("axistype", "cartesian");
        tipCyl.set("axis", new double[]{1, 0, 0});
        tipCyl.set("pos", new double[]{0.055, 0, 0});
        tipCyl.set("r", rProp);
        tipCyl.set("h", 0.085);

        double[] etas = {0.24, 0.40, 0.56, 0.72, 0.88, 1.05};
        String[] wpNames = new String[etas.length];
        int nHalf = 24;
        for (int si = 0; si < etas.length; si++) {
            double eta = etas[si];
            double r = eta * rProp;
            double etaClamped = Math.min(1.0, Math.max(0.4, eta));
            double pitchDeg = interpQuadSection(etaClamped, pitch04, pitch07, pitch10);
            double chord = interpQuadSection(etaClamped, chord04, chord07, chord10);
            double sSpan = Math.max(0.0, (r - rHub) / (rProp - rHub));
            double skewRad = Math.toRadians(skewTip) * Math.pow(sSpan, 1.4);
            double rakeM = rakeTip * sSpan;
            double xCenter = 0.0975 + rakeM;
            double beta = Math.toRadians(pitchDeg);
            double teHalf = Math.max(0.00045, 0.045 * chord * thick);

            String wtag = "wp_sec_" + si;
            wpNames[si] = wtag;
            GeomFeature wp = g.feature().create(wtag, "WorkPlane");
            wp.set("planetype", "coordinates");
            wp.set("genpoints", new double[][]{
                {xCenter, r * Math.cos(skewRad), r * Math.sin(skewRad)},
                {xCenter + 1.0, r * Math.cos(skewRad), r * Math.sin(skewRad)},
                {xCenter, r * Math.cos(skewRad) - Math.sin(skewRad), r * Math.sin(skewRad) + Math.cos(skewRad)}
            });
            wp.set("unite", "on");

            String[][] upperTbl = new String[nHalf + 1][2];
            String[][] lowerTbl = new String[nHalf + 1][2];
            for (int k = 0; k <= nHalf; k++) {
                double s = 0.5 * (1.0 - Math.cos(Math.PI * k / nHalf));
                double yc = 4.0 * camber * chord * s * (1.0 - s);
                double ytBase = 5.0 * thick * chord * (
                    0.2969 * Math.sqrt(Math.max(0.0, s))
                    - 0.1260 * s - 0.3516 * s * s + 0.2843 * s * s * s - 0.1036 * s * s * s * s
                );
                double yt = ytBase + s * teHalf;
                double u = (0.5 - s) * chord;
                double vu = yc + yt;
                double vl = yc - yt;
                upperTbl[k][0] = Double.toString(u * Math.sin(beta) + vu * Math.cos(beta));
                upperTbl[k][1] = Double.toString(u * Math.cos(beta) - vu * Math.sin(beta));
                lowerTbl[k][0] = Double.toString(u * Math.sin(beta) + vl * Math.cos(beta));
                lowerTbl[k][1] = Double.toString(u * Math.cos(beta) - vl * Math.sin(beta));
            }
            GeomFeature icU = wp.geom().feature().create("ic_u", "InterpolationCurve");
            icU.set("type", "open");
            icU.set("source", "table");
            icU.set("table", upperTbl);

            GeomFeature icL = wp.geom().feature().create("ic_l", "InterpolationCurve");
            icL.set("type", "open");
            icL.set("source", "table");
            icL.set("table", lowerTbl);

            GeomFeature teLine = wp.geom().feature().create("te_seg", "Polygon");
            teLine.set("type", "open");
            teLine.set("source", "table");
            teLine.set("table", new String[][]{upperTbl[nHalf], lowerTbl[nHalf]});

            GeomFeature cs = wp.geom().feature().create("cs", "ConvertToSolid");
            cs.selection("input").set(new String[]{"ic_u", "ic_l", "te_seg"});
        }

        GeomFeature loft = g.feature().create("blade_raw", "Loft");
        loft.selection("profile").set(wpNames);
        loft.set("type", "solid");
        loft.set("crossfaces", "off");
        loft.set("removeredundant", "off");

        GeomFeature bladeTrim = g.feature().create("blade0", "Intersection");
        bladeTrim.selection("input").set(new String[]{"blade_raw", "tip_cyl"});
        bladeTrim.set("intbnd", "off");
        bladeTrim.set("geomrep", "cadps");

        GeomFeature bladesRot = g.feature().create("blades_rot", "Rotate");
        bladesRot.selection("input").set(new String[]{"blade0"});
        bladesRot.set("axistype", "cartesian");
        bladesRot.set("axis", new double[]{1, 0, 0});
        bladesRot.set("rot", new double[]{0, 120, 240});

        GeomFeature impUnion = g.feature().create("imp_solid", "Union");
        impUnion.selection("input").set(new String[]{"hub_cyl", "blades_rot"});
        impUnion.set("intbnd", "off");
        impUnion.set("geomrep", "cadps");

        double xLe = 0.009966, xTe = 0.202606;
        double rOutMax = 0.1940, rOutTe = 0.1865;
        double rInletLip = Math.min(0.184, rThroat * lipRatio);
        double rDiffExit = Math.max(rThroat + 0.0015, rThroat + 0.055 * Math.tan(Math.toRadians(diffAngle)) + 0.0055);
        if (rDiffExit > rOutTe - 0.004) rDiffExit = rOutTe - 0.004;

        List<double[]> dpts = new ArrayList<>();
        double xNoseEnd = 0.025;
        for (int i = 0; i < 8; i++) {
            double a = -0.5 * Math.PI + Math.PI * i / 8.0;
            double x = xNoseEnd - (xNoseEnd - xLe) * Math.cos(a);
            double r = 0.5 * (rInletLip + rOutMax) + 0.5 * (rOutMax - rInletLip) * Math.sin(a);
            dpts.add(new double[]{x, r});
        }
        double xTeStart = 0.192;
        for (int i = 0; i <= 10; i++) {
            double t = i / 10.0;
            double x = xNoseEnd + t * (xTeStart - xNoseEnd);
            double r = rOutMax + (rOutTe - rOutMax) * (t * t * (3.0 - 2.0 * t));
            dpts.add(new double[]{x, r});
        }
        for (int i = 1; i < 6; i++) {
            double a = 0.5 * Math.PI - Math.PI * i / 6.0;
            double x = xTeStart + (xTe - xTeStart) * Math.cos(a);
            double r = 0.5 * (rDiffExit + rOutTe) + 0.5 * (rOutTe - rDiffExit) * Math.sin(a);
            dpts.add(new double[]{x, r});
        }
        double xThroatEnd = 0.134;
        for (int i = 0; i <= 8; i++) {
            double t = i / 8.0;
            double x = xTeStart - t * (xTeStart - xThroatEnd);
            double r = rDiffExit + (rThroat - rDiffExit) * (t * t * (3.0 - 2.0 * t));
            dpts.add(new double[]{x, r});
        }
        double xThroatStart = 0.060;
        for (int i = 1; i <= 10; i++) {
            double t = i / 10.0;
            dpts.add(new double[]{xThroatEnd - t * (xThroatEnd - xThroatStart), rThroat});
        }
        for (int i = 1; i < 8; i++) {
            double t = i / 8.0;
            double x = xThroatStart - t * (xThroatStart - xNoseEnd);
            double r = rThroat + (rInletLip - rThroat) * (t * t * (3.0 - 2.0 * t));
            dpts.add(new double[]{x, r});
        }
        String[][] dtbl = new String[dpts.size()][2];
        for (int k = 0; k < dpts.size(); k++) {
            dtbl[k][0] = Double.toString(dpts.get(k)[0]);
            dtbl[k][1] = Double.toString(dpts.get(k)[1]);
        }

        GeomFeature wpDuct = g.feature().create("wp_duct", "WorkPlane");
        wpDuct.set("planetype", "quick");
        wpDuct.set("quickplane", "xy");
        wpDuct.set("unite", "on");
        GeomFeature ic2d = wpDuct.geom().feature().create("ic_duct", "InterpolationCurve");
        ic2d.set("type", "closed");
        ic2d.set("source", "table");
        ic2d.set("table", dtbl);
        GeomFeature cs2d = wpDuct.geom().feature().create("cs_duct", "ConvertToSolid");
        cs2d.selection("input").set(new String[]{"ic_duct"});

        GeomFeature ductRev = g.feature().create("duct_solid", "Revolve");
        ductRev.selection("input").set(new String[]{"wp_duct"});
        ductRev.set("axistype", "3d");
        ductRev.set("pos3", new double[]{0, 0, 0});
        ductRev.set("axis3", new double[]{1, 0, 0});
        ductRev.set("angtype", "full");

        GeomFeature cutbox = g.feature().create("cutbox", "Difference");
        cutbox.selection("input").set(new String[]{"far_cyl"});
        cutbox.selection("input2").set(new String[]{"rot_cyl"});
        cutbox.set("keepsubtract", "on");
        cutbox.set("geomrep", "cadps");

        GeomFeature station = g.feature().create("station", "Difference");
        station.selection("input").set(new String[]{"cutbox"});
        station.selection("input2").set(new String[]{"duct_solid"});
        station.set("keepsubtract", "off");
        station.set("geomrep", "cadps");

        GeomFeature rotorflow = g.feature().create("rotorflow", "Difference");
        rotorflow.selection("input").set(new String[]{"rot_cyl"});
        rotorflow.selection("input2").set(new String[]{"imp_solid"});
        rotorflow.set("keepsubtract", "off");
        rotorflow.set("geomrep", "cadps");

        GeomFeature fin = g.feature("fin");
        fin.set("action", "union");
        fin.set("createpairs", "on");
        fin.set("pairtype", "identity");
        fin.set("geomrep", "cadps");
        fin.set("frame", "spatial");

        g.run();
        if (g.getNDomains() != 2) {
            throw new IllegalStateException("Expected exactly 2 fluid domains after parametric rebuild, got " + g.getNDomains());
        }
        return new double[]{rProp, rThroat, rRotorflow};
    }

    private static void reclassifyAndBindSelections(
            Model model, ModelNode comp, GeomSequence g,
            double rProp, double rThroat, double rRotorflow) {
        if (containsTag(comp.selection().tags(), "tmp_prop_box_mov")) {
            comp.selection().remove("tmp_prop_box_mov");
        }
        SelectionFeature boxMov = comp.selection().create("tmp_prop_box_mov", "Box");
        boxMov.set("entitydim", 3);
        boxMov.set("condition", "inside");
        boxMov.set("xmin", 0.054); boxMov.set("xmax", 0.141);
        boxMov.set("ymin", -0.168); boxMov.set("ymax", 0.168);
        boxMov.set("zmin", -0.168); boxMov.set("zmax", 0.168);
        int[] movArr = sortedCopy(comp.selection("tmp_prop_box_mov").entities());
        comp.selection().remove("tmp_prop_box_mov");
        if (movArr.length == 0) {
            throw new IllegalStateException("Spatial Box classification found zero moving domains");
        }
        int[] allArr = new int[g.getNDomains()];
        for (int d = 0; d < allArr.length; d++) {
            allArr[d] = d + 1;
        }

        if (containsTag(comp.mesh().tags(), "tmp_prop_cls_mesh")) {
            comp.mesh().remove("tmp_prop_cls_mesh");
        }
        MeshSequence tmpMesh = comp.mesh().create("tmp_prop_cls_mesh", g.tag());
        tmpMesh.feature("size").set("hauto", 5);
        MeshFeature ftri = tmpMesh.feature().create("ftri", "FreeTri");
        ftri.selection().geom(g.tag(), 2);
        ftri.selection().all();
        tmpMesh.run();

        safeRemoveDataset(model, "tmp_prop_cls_ds");
        DatasetFeature ds = model.result().dataset().create("tmp_prop_cls_ds", "Mesh");
        ds.set("mesh", "tmp_prop_cls_mesh");

        String[] exprs = {"x", "sqrt(y^2+z^2)"};
        List<Integer> farBnds = new ArrayList<>();
        List<Integer> planesBnds = new ArrayList<>();
        List<Integer> joinBnds = new ArrayList<>();
        List<Integer> impBnds = new ArrayList<>();
        List<Integer> nozBnds = new ArrayList<>();
        List<Integer> unclassBnds = new ArrayList<>();

        int nbnd = g.getNBoundaries();
        for (int b = 1; b <= nbnd; b++) {
            double[] mm = evalSurfaceMinMax(model, "tmp_prop_cls_ds", null, new int[]{b}, exprs);
            double xmin = mm[0], xmax = mm[1], rmin = mm[2], rmax = mm[3];
            if (xmax <= -3.20 || xmin >= 1.40 || rmax >= 1.40) {
                farBnds.add(b);
            } else if (Math.abs(xmin - 0.055) < 5e-4 && Math.abs(xmax - 0.055) < 5e-4 && rmax <= rRotorflow + 0.002) {
                planesBnds.add(b);
                joinBnds.add(b);
            } else if (Math.abs(xmin - 0.140) < 5e-4 && Math.abs(xmax - 0.140) < 5e-4 && rmax <= rRotorflow + 0.002) {
                planesBnds.add(b);
                joinBnds.add(b);
            } else if (xmin >= 0.054 && xmax <= 0.141 && Math.abs(rmax - rRotorflow) < 6e-4 && rmin >= rProp - 0.015) {
                joinBnds.add(b);
            } else if (xmin >= 0.0555 && xmax <= 0.1395 && rmax <= rProp + 5e-4) {
                impBnds.add(b);
            } else if (xmin >= 0.005 && xmax <= 0.210 && rmax >= rThroat - 2e-4 && rmax <= 0.210) {
                nozBnds.add(b);
            } else {
                unclassBnds.add(b);
            }
        }
        safeRemoveDataset(model, "tmp_prop_cls_ds");
        comp.mesh().remove("tmp_prop_cls_mesh");

        if (!unclassBnds.isEmpty() || farBnds.size() < 3 || planesBnds.size() < 2 || impBnds.isEmpty() || nozBnds.isEmpty()) {
            throw new IllegalStateException(
                "Boundary classification incomplete: far=" + farBnds.size()
                + " join=" + joinBnds.size() + " planes=" + planesBnds.size()
                + " imp=" + impBnds.size() + " noz=" + nozBnds.size()
                + " unclass=" + unclassBnds
            );
        }

        int[] farArr = toSortedIntArray(farBnds);
        int[] joinArr = toSortedIntArray(joinBnds);
        int[] planesArr = toSortedIntArray(planesBnds);
        int[] impArr = toSortedIntArray(impBnds);
        int[] nozArr = toSortedIntArray(nozBnds);

        if (containsTag(comp.selection().tags(), "moving")) comp.selection("moving").set(movArr);
        if (containsTag(comp.selection().tags(), "far")) comp.selection("far").set(farArr);
        if (containsTag(comp.selection().tags(), "join")) comp.selection("join").set(joinArr);
        if (containsTag(comp.selection().tags(), "planes")) comp.selection("planes").set(planesArr);
        if (containsTag(comp.selection().tags(), "impeller")) comp.selection("impeller").set(impArr);
        if (containsTag(comp.selection().tags(), "nozzle")) comp.selection("nozzle").set(nozArr);

        if (containsTag(comp.common().tags(), "rot1")) safeSetSel(comp.common("rot1").selection(), movArr);
        if (containsTag(comp.cpl().tags(), "intR")) safeSetSel(comp.cpl("intR").selection(), impArr);
        if (containsTag(comp.cpl().tags(), "intD")) safeSetSel(comp.cpl("intD").selection(), nozArr);
        if (containsTag(comp.cpl().tags(), "intFar")) safeSetSel(comp.cpl("intFar").selection(), farArr);
        if (containsTag(comp.cpl().tags(), "minOp1")) safeSetSel(comp.cpl("minOp1").selection(), movArr);
        if (containsTag(comp.cpl().tags(), "maxOp1")) safeSetSel(comp.cpl("maxOp1").selection(), movArr);

        Physics spf = comp.physics("spf");
        if (containsTag(spf.feature().tags(), "fp1")) safeSetSel(spf.feature("fp1").selection(), allArr);
        if (containsTag(spf.feature().tags(), "init1")) safeSetSel(spf.feature("init1").selection(), allArr);
        if (containsTag(spf.feature().tags(), "blade")) safeSetSel(spf.feature("blade").selection(), impArr);
        if (containsTag(spf.feature().tags(), "duct")) safeSetSel(spf.feature("duct").selection(), nozArr);

        String meshTag = containsTag(comp.mesh().tags(), "mesh") ? "mesh" : comp.mesh().tags()[0];
        MeshSequence ms = comp.mesh(meshTag);
        if (containsTag(ms.feature().tags(), "bulk")) safeSetSel(ms.feature("bulk").selection(), allArr);
        if (containsTag(ms.feature().tags(), "tet")) safeSetSel(ms.feature("tet").selection(), allArr);
        if (containsTag(ms.feature().tags(), "bl")) safeSetSel(ms.feature("bl").selection(), allArr);

        // Update built-in numerical evaluators if present
        String[] numTags = model.result().numerical().tags();
        if (containsTag(numTags, "auditRotor")) safeSetSel(model.result().numerical("auditRotor").selection(), impArr);
        if (containsTag(numTags, "auditDuct")) safeSetSel(model.result().numerical("auditDuct").selection(), nozArr);
        for (String ft : new String[]{"net", "gross", "momentum", "pressureReaction"}) {
            if (containsTag(numTags, ft)) safeSetSel(model.result().numerical(ft).selection(), farArr);
        }
        if (planesArr.length >= 2) {
            for (String p0 : new String[]{"x10", "vel10", "mdot10"}) {
                if (containsTag(numTags, p0)) safeSetSel(model.result().numerical(p0).selection(), new int[]{planesArr[0]});
            }
            for (String p1 : new String[]{"x26", "vel26", "mdot26"}) {
                if (containsTag(numTags, p1)) safeSetSel(model.result().numerical(p1).selection(), new int[]{planesArr[1]});
            }
        }
    }

    public static void handleInspect(String modelPath) {
        if (modelPath == null || modelPath.isEmpty()) {
            ComsolBridge.outputError("MODEL_NOT_FOUND", "Target model file not found: " + modelPath, "");
            return;
        }
        Model model = null;
        try {
            try {
                model = ModelUtil.loadCopy("Model", modelPath);
            } catch (Throwable t) {
                String msg = t.getMessage() != null ? t.getMessage() : "";
                if (msg.toLowerCase().contains("not found") || msg.toLowerCase().contains("no such file") || msg.toLowerCase().contains("cannot find")) {
                    ComsolBridge.outputError("MODEL_NOT_FOUND", "Target model file not found: " + modelPath, ComsolBridge.getStackTrace(t));
                } else {
                    ComsolBridge.outputError("COMSOL_CONNECTION_FAILED", "Failed to load model: " + msg, ComsolBridge.getStackTrace(t));
                }
                return;
            }
            if (!containsTag(model.component().tags(), "c")) {
                ComsolBridge.outputError("NODE_NOT_FOUND", "Component 'c' not found in propeller model", "");
                return;
            }
            ModelNode comp = model.component("c");
            String geomTag = containsTag(comp.geom().tags(), "g") ? "g" : comp.geom().tags()[0];
            String meshTag = containsTag(comp.mesh().tags(), "mesh") ? "mesh" : comp.mesh().tags()[0];
            GeomSequence geom = comp.geom(geomTag);

            String dsetTag = ensureEvalDataset(model, "c", meshTag, "sol1", false);

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"inspect_propeller\",\n");
            sb.append("  \"model_path\": ").append(ComsolBridge.quote(modelPath)).append(",\n");
            sb.append("  \"model_tag\": ").append(ComsolBridge.quote(model.tag())).append(",\n");
            sb.append("  \"comsol_version\": ").append(ComsolBridge.quote(ModelUtil.getComsolVersion())).append(",\n");
            sb.append("  \"components\": ").append(ComsolBridge.stringArrayToJson(model.component().tags())).append(",\n");

            // Parameters
            sb.append("  \"parameters\": {\n");
            String[] pnames = model.param().varnames();
            for (int i = 0; i < pnames.length; i++) {
                String pname = pnames[i];
                String expr = model.param().get(pname);
                String descr = "";
                try { descr = model.param().descr(pname); } catch (Throwable ignored) {}
                double val = Double.NaN;
                String unit = "";
                try { val = model.param().evaluate(pname); } catch (Throwable ignored) {}
                try { unit = model.param().evaluateUnit(pname); } catch (Throwable ignored) {}
                sb.append("    ").append(ComsolBridge.quote(pname)).append(": {");
                sb.append("\"expr\":").append(ComsolBridge.quote(expr));
                sb.append(",\"value\":").append(Double.isFinite(val) ? Double.toString(val) : "null");
                sb.append(",\"unit\":").append(ComsolBridge.quote(unit != null ? unit : ""));
                sb.append(",\"descr\":").append(ComsolBridge.quote(descr != null ? descr : ""));
                sb.append("}").append(i < pnames.length - 1 ? "," : "").append("\n");
            }
            sb.append("  },\n");

            // Geometry
            String impFilename = "";
            String impCadType = "";
            String finAction = "";
            if (containsTag(geom.feature().tags(), "imp")) {
                try { impFilename = geom.feature("imp").getString("filename"); } catch (Throwable ignored) {}
                try { impCadType = geom.feature("imp").getString("cadtype"); } catch (Throwable ignored) {}
            }
            if (containsTag(geom.feature().tags(), "fin")) {
                try { finAction = geom.feature("fin").getString("action"); } catch (Throwable ignored) {}
            }
            sb.append("  \"geometry\": {\n");
            sb.append("    \"tag\": ").append(ComsolBridge.quote(geomTag)).append(",\n");
            sb.append("    \"sdim\": ").append(geom.getSDim()).append(",\n");
            sb.append("    \"length_unit\": ").append(ComsolBridge.quote(geom.lengthUnit())).append(",\n");
            sb.append("    \"bounding_box\": ").append(ComsolBridge.doubleArrayToJson(geom.getBoundingBox())).append(",\n");
            sb.append("    \"n_domains\": ").append(geom.getNDomains()).append(",\n");
            sb.append("    \"n_boundaries\": ").append(geom.getNBoundaries()).append(",\n");
            sb.append("    \"n_edges\": ").append(geom.getNEdges()).append(",\n");
            sb.append("    \"n_vertices\": ").append(geom.getNVertices()).append(",\n");
            sb.append("    \"feature_tags\": ").append(ComsolBridge.stringArrayToJson(geom.feature().tags())).append(",\n");
            sb.append("    \"imp_filename\": ").append(ComsolBridge.quote(impFilename)).append(",\n");
            sb.append("    \"imp_cadtype\": ").append(ComsolBridge.quote(impCadType)).append(",\n");
            sb.append("    \"fin_action\": ").append(ComsolBridge.quote(finAction)).append("\n");
            sb.append("  },\n");

            // Selections
            sb.append("  \"selections\": {\n");
            String[] selTags = comp.selection().tags();
            for (int i = 0; i < selTags.length; i++) {
                String stag = selTags[i];
                int dim = -1;
                int[] ents = new int[0];
                try {
                    int[] dims = comp.selection(stag).dimension();
                    if (dims != null && dims.length > 0) dim = dims[0];
                } catch (Throwable ignored) {}
                try { ents = sortedCopy(comp.selection(stag).entities()); } catch (Throwable ignored) {}
                sb.append("    ").append(ComsolBridge.quote(stag)).append(": {\"dimension\":").append(dim)
                  .append(",\"entities\":").append(ComsolBridge.intArrayToJson(ents)).append("}")
                  .append(i < selTags.length - 1 ? "," : "").append("\n");
            }
            sb.append("  },\n");

            // Common (rot1)
            sb.append("  \"common\": {\n");
            String[] commonTags = comp.common().tags();
            String rot1Json = "null";
            for (int i = 0; i < commonTags.length; i++) {
                String ctag = commonTags[i];
                CommonFeature cf = comp.common(ctag);
                int[] ents = new int[0];
                try { ents = sortedCopy(cf.selection().entities()); } catch (Throwable ignored) {}
                String revPerTime = "";
                String[] rotAxis = new String[0];
                String[] rotBase = new String[0];
                try { revPerTime = cf.getString("revolutionsPerTime"); } catch (Throwable ignored) {}
                try { rotAxis = cf.getStringArray("rotationAxis"); } catch (Throwable ignored) {}
                try { rotBase = cf.getStringArray("rotationAxisBasePoint"); } catch (Throwable ignored) {}
                String entryJson = "{\"type\":" + ComsolBridge.quote(cf.getType())
                    + ",\"selection\":" + ComsolBridge.intArrayToJson(ents)
                    + ",\"revolutionsPerTime\":" + ComsolBridge.quote(revPerTime != null ? revPerTime : "")
                    + ",\"rotationAxis\":" + ComsolBridge.stringArrayToJson(rotAxis)
                    + ",\"rotationAxisBasePoint\":" + ComsolBridge.stringArrayToJson(rotBase) + "}";
                if ("rot1".equals(ctag)) {
                    rot1Json = entryJson;
                }
                sb.append("    ").append(ComsolBridge.quote(ctag)).append(": ").append(entryJson)
                  .append(i < commonTags.length - 1 ? "," : "").append("\n");
            }
            sb.append("  },\n");
            sb.append("  \"rot1\": ").append(rot1Json).append(",\n");

            // Couplings
            sb.append("  \"coupling_operators\": {\n");
            String[] cplTags = comp.cpl().tags();
            for (int i = 0; i < cplTags.length; i++) {
                String tag = cplTags[i];
                Cpl cpl = comp.cpl(tag);
                int[] ents = new int[0];
                try { ents = sortedCopy(cpl.selection().entities()); } catch (Throwable ignored) {}
                String opname = "";
                String label = "";
                try { opname = cpl.getString("opname"); } catch (Throwable ignored) {}
                try { label = cpl.label(); } catch (Throwable ignored) {}
                sb.append("    ").append(ComsolBridge.quote(tag)).append(": {\"type\":")
                  .append(ComsolBridge.quote(cpl.getType()))
                  .append(",\"opname\":").append(ComsolBridge.quote(opname != null ? opname : ""))
                  .append(",\"label\":").append(ComsolBridge.quote(label != null ? label : ""))
                  .append(",\"entities\":").append(ComsolBridge.intArrayToJson(ents)).append("}")
                  .append(i < cplTags.length - 1 ? "," : "").append("\n");
            }
            sb.append("  },\n");

            // Physics BCs (spf)
            sb.append("  \"physics_bcs\": {\n");
            if (containsTag(comp.physics().tags(), "spf")) {
                Physics phys = comp.physics("spf");
                String[] ftags = phys.feature().tags();
                String[] watchProps = {"BoundaryCondition", "PressureType", "p0", "SuppressBackflow", "f0", "WallCondition"};
                for (int i = 0; i < ftags.length; i++) {
                    String ftag = ftags[i];
                    PhysicsFeature pf = phys.feature(ftag);
                    int[] ents = new int[0];
                    try { ents = sortedCopy(pf.selection().entities()); } catch (Throwable ignored) {}
                    sb.append("    ").append(ComsolBridge.quote(ftag)).append(": {\"type\":")
                      .append(ComsolBridge.quote(pf.getType()))
                      .append(",\"selection_entities\":").append(ComsolBridge.intArrayToJson(ents))
                      .append(",\"properties\":{");
                    List<String> available = Arrays.asList(pf.properties());
                    boolean firstProp = true;
                    for (String wp : watchProps) {
                        if (available.contains(wp)) {
                            if (!firstProp) sb.append(",");
                            firstProp = false;
                            String pval = "";
                            try { pval = pf.getString(wp); } catch (Throwable ignored) {}
                            sb.append(ComsolBridge.quote(wp)).append(":").append(ComsolBridge.quote(pval != null ? pval : ""));
                        }
                    }
                    sb.append("}}").append(i < ftags.length - 1 ? "," : "").append("\n");
                }
            }
            sb.append("  },\n");

            // 3D Surface & Volume Bounding Boxes + Far Face Classification
            String[] exprs = {"x", "y", "z", "sqrt(y^2+z^2)"};
            sb.append("  \"bounding_boxes\": {\n");
            String[] surfSels = {"impeller", "nozzle", "far", "planes"};
            for (int i = 0; i < surfSels.length; i++) {
                String selName = surfSels[i];
                sb.append("    ").append(ComsolBridge.quote(selName)).append(": ");
                if (containsTag(comp.selection().tags(), selName)) {
                    double[] mm = evalSurfaceMinMax(model, dsetTag, selName, null, exprs);
                    sb.append(boundsJson(mm));
                } else {
                    sb.append("null");
                }
                sb.append(",\n");
            }
            int[][] farSplit = classifyFarBoundaries(model, comp, dsetTag);
            sb.append("    \"far_neg_x_cap\": ").append(ComsolBridge.intArrayToJson(farSplit[0])).append(",\n");
            sb.append("    \"far_pos_x_cap\": ").append(ComsolBridge.intArrayToJson(farSplit[1])).append(",\n");
            sb.append("    \"far_lateral\": ").append(ComsolBridge.intArrayToJson(farSplit[2])).append("\n");
            sb.append("  },\n");
            sb.append("  \"geometry_audit\": ").append(buildGeometryAuditJson(model, comp, dsetTag)).append(",\n");

            // Mesh summary
            MeshSequence mesh = comp.mesh(meshTag);
            sb.append("  \"mesh\": {\"tag\":").append(ComsolBridge.quote(meshTag))
              .append(",\"is_complete\":").append(mesh.isComplete())
              .append(",\"num_elem\":").append(mesh.getNumElem())
              .append(",\"num_vertex\":").append(mesh.getNumVertex())
              .append(",\"min_quality\":").append(mesh.getMinQuality())
              .append(",\"mean_quality\":").append(mesh.getMeanQuality()).append("},\n");

            // Studies & Solvers
            sb.append("  \"studies_and_solvers\": {\n");
            sb.append("    \"study_tags\": ").append(ComsolBridge.stringArrayToJson(model.study().tags())).append(",\n");
            if (containsTag(model.study().tags(), "std") && containsTag(model.study("std").feature().tags(), "frrot")) {
                StudyFeature frrot = model.study("std").feature("frrot");
                sb.append("    \"frrot\": {\"usestol\":").append(ComsolBridge.quote(frrot.getString("usestol")))
                  .append(",\"stol\":").append(ComsolBridge.quote(frrot.getString("stol")))
                  .append(",\"mesh\":").append(ComsolBridge.stringArrayToJson(frrot.getStringArray("mesh"))).append("},\n");
            } else {
                sb.append("    \"frrot\": null,\n");
            }
            sb.append("    \"sol_tags\": ").append(ComsolBridge.stringArrayToJson(model.sol().tags())).append(",\n");
            if (containsTag(model.sol().tags(), "sol1")) {
                SolverSequence sol1 = model.sol("sol1");
                sb.append("    \"sol1\": {\"is_empty\":").append(sol1.isEmpty())
                  .append(",\"size\":").append(ComsolBridge.intArrayToJson(sol1.getSize()))
                  .append(",\"feature_tags\":").append(ComsolBridge.stringArrayToJson(sol1.feature().tags()));
                if (containsTag(sol1.feature().tags(), "v1")) {
                    SolverFeature v1 = sol1.feature("v1");
                    sb.append(",\"v1\":{\"control\":").append(ComsolBridge.quote(readSolverProp(v1, "control")))
                      .append(",\"initmethod\":").append(ComsolBridge.quote(readSolverProp(v1, "initmethod")))
                      .append(",\"initsol\":").append(ComsolBridge.quote(readSolverProp(v1, "initsol")))
                      .append(",\"initsoluse\":").append(ComsolBridge.quote(readSolverProp(v1, "initsoluse")))
                      .append(",\"solnum\":").append(ComsolBridge.quote(readSolverProp(v1, "solnum"))).append("}");
                }
                if (containsTag(sol1.feature().tags(), "s1")) {
                    SolverFeature s1 = sol1.feature("s1");
                    sb.append(",\"s1\":{\"control\":").append(ComsolBridge.quote(readSolverProp(s1, "control")))
                      .append(",\"stol\":").append(ComsolBridge.quote(readSolverProp(s1, "stol")));
                    if (containsTag(s1.feature().tags(), "se1")) {
                        SolverFeature se1 = s1.feature("se1");
                        sb.append(",\"segcflaa\":").append(ComsolBridge.quote(readSolverProp(se1, "segcflaa")))
                          .append(",\"submincfl\":").append(ComsolBridge.quote(readSolverProp(se1, "submincfl")))
                          .append(",\"maxsegiter\":").append(ComsolBridge.quote(readSolverProp(se1, "maxsegiter")));
                        if (containsTag(se1.feature().tags(), "ss1")) {
                            sb.append(",\"ss1_subdamp\":").append(ComsolBridge.quote(readSolverProp(se1.feature("ss1"), "subdamp")));
                        }
                        if (containsTag(se1.feature().tags(), "ss2")) {
                            sb.append(",\"ss2_subdamp\":").append(ComsolBridge.quote(readSolverProp(se1.feature("ss2"), "subdamp")));
                        }
                    }
                    sb.append("}");
                }
                sb.append("},\n");
            } else {
                sb.append("    \"sol1\": null,\n");
            }
            if (containsTag(model.sol().tags(), "seed")) {
                SolverSequence seed = model.sol("seed");
                sb.append("    \"seed\": {\"exists\":true,\"is_empty\":").append(seed.isEmpty())
                  .append(",\"size\":").append(ComsolBridge.intArrayToJson(seed.getSize())).append("}\n");
            } else {
                sb.append("    \"seed\": {\"exists\":false,\"is_empty\":true,\"size\":[]}\n");
            }
            sb.append("  },\n");

            // Numerical evaluators
            sb.append("  \"numerical_evaluators\": {\n");
            String[] numTags = model.result().numerical().tags();
            List<String> cleanNumTags = new ArrayList<>();
            for (String nt : numTags) {
                if (!nt.startsWith("tmp_prop_")) cleanNumTags.add(nt);
            }
            for (int i = 0; i < cleanNumTags.size(); i++) {
                String nt = cleanNumTags.get(i);
                NumericalFeature nf = model.result().numerical(nt);
                String[] units = new String[0];
                try { units = nf.getStringArray("unit"); } catch (Throwable ignored) {}
                sb.append("    ").append(ComsolBridge.quote(nt)).append(": {\"type\":")
                  .append(ComsolBridge.quote(nf.getType()))
                  .append(",\"units\":").append(ComsolBridge.stringArrayToJson(units)).append("}")
                  .append(i < cleanNumTags.size() - 1 ? "," : "").append("\n");
            }
            sb.append("  }\n");

            sb.append("}\n");
            ComsolBridge.emitResult(sb.toString());
        } catch (Throwable t) {
            ComsolBridge.outputError(
                "COMSOL_CONNECTION_FAILED",
                "Failed to inspect propeller model: " + t.getMessage(),
                ComsolBridge.getStackTrace(t)
            );
        } finally {
            if (model != null) {
                try { cleanupTempNodes(model); } catch (Throwable ignored) {}
            }
            try { ModelUtil.clear(); } catch (Throwable ignored) {}
        }
    }

    public static void handleEval(String modelPath, String outputPathOrDash, String encodedSpec, String command) {
        boolean isBaselineCmd = "run-propeller-baseline".equals(command);
        String operation = isBaselineCmd ? "run_propeller_baseline" : "eval_propeller";

        EvalSpec spec;
        try {
            spec = new EvalSpec(encodedSpec, isBaselineCmd);
        } catch (Throwable t) {
            ComsolBridge.outputError("INVALID_PARAMETER", "Invalid propeller eval spec: " + t.getMessage(), ComsolBridge.getStackTrace(t));
            return;
        }

        if (modelPath == null || modelPath.isEmpty()) {
            ComsolBridge.outputError("MODEL_NOT_FOUND", "Source model file not found: " + modelPath, "");
            return;
        }
        if (spec.saveOutput) {
            if (outputPathOrDash == null || outputPathOrDash.isEmpty() || "-".equals(outputPathOrDash)) {
                ComsolBridge.outputError("INVALID_PARAMETER", "save_output=true requires an explicit output .mph path", "");
                return;
            }
            try {
                ComsolBridge.branchDistinctPaths(modelPath, outputPathOrDash);
            } catch (Throwable t) {
                ComsolBridge.outputError("MODEL_VALIDATION_FAILED", t.getMessage(), "");
                return;
            }
        }

        Model model = null;
        try {
            try {
                model = ModelUtil.loadCopy("Model", modelPath);
            } catch (Throwable t) {
                String msg = t.getMessage() != null ? t.getMessage() : "";
                if (msg.toLowerCase().contains("not found") || msg.toLowerCase().contains("no such file") || msg.toLowerCase().contains("cannot find")) {
                    ComsolBridge.outputError("MODEL_NOT_FOUND", "Source model file not found: " + modelPath, ComsolBridge.getStackTrace(t));
                } else {
                    ComsolBridge.outputError("COMSOL_CONNECTION_FAILED", "Failed to load model: " + msg, ComsolBridge.getStackTrace(t));
                }
                return;
            }
            if (!containsTag(model.component().tags(), "c")) {
                ComsolBridge.outputError("NODE_NOT_FOUND", "Component 'c' not found", "");
                return;
            }
            ModelNode comp = model.component("c");
            if (!containsTag(comp.geom().tags(), "g")
                    || !containsTag(comp.physics().tags(), "spf")
                    || !containsTag(model.sol().tags(), "sol1")) {
                ComsolBridge.outputError("NODE_NOT_FOUND", "Required node (g, spf, or sol1) not found", "");
                return;
            }

            GeomSequence geom = comp.geom("g");
            String meshTag = containsTag(comp.mesh().tags(), "mesh") ? "mesh" : comp.mesh().tags()[0];
            boolean geometryRebuilt = false;

            // Inject parameters BEFORE geometry rebuild so CAD/parametric sequence uses updated values
            for (Map.Entry<String, String> entry : spec.extraParams.entrySet()) {
                model.param().set(entry.getKey(), entry.getValue());
                model.param().evaluate(entry.getKey());
            }

            if (spec.rebuildGeometry) {
                clearStaleSolutions(model);
                double[] geomRadii = rebuildParametricGeometry(model, comp, geom);
                reclassifyAndBindSelections(model, comp, geom, geomRadii[0], geomRadii[1], geomRadii[2]);
                MeshSequence targetMesh = comp.mesh(meshTag);
                targetMesh.run();
                if (!targetMesh.isComplete() || targetMesh.getNumElem() <= 0 || targetMesh.getMinQuality() <= 0.0) {
                    ComsolBridge.outputError(
                        "MODEL_VALIDATION_FAILED",
                        "Rebuilt mesh validation failed: complete=" + targetMesh.isComplete()
                            + " num_elem=" + targetMesh.getNumElem()
                            + " min_quality=" + targetMesh.getMinQuality(),
                        ""
                    );
                    return;
                }
                geometryRebuilt = true;
            } else if (!spec.cadPath.isEmpty()) {
                if (!containsTag(geom.feature().tags(), "imp")) {
                    ComsolBridge.outputError("NODE_NOT_FOUND", "Geometry import feature 'imp' not found", "");
                    return;
                }
                clearStaleSolutions(model);
                geom.feature("imp").set("cadtype", spec.cadType);
                geom.feature("imp").set("filename", spec.cadPath);
                geom.run();
                double tipClear = readParamWithUnitFactor(model, "tip_clearance_m", "m", 0.0030);
                reclassifyAndBindSelections(model, comp, geom, 0.160, 0.160 + tipClear, 0.1625);
                MeshSequence targetMesh = comp.mesh(meshTag);
                targetMesh.run();
                if (!targetMesh.isComplete() || targetMesh.getNumElem() <= 0 || targetMesh.getMinQuality() <= 0.0) {
                    ComsolBridge.outputError(
                        "MODEL_VALIDATION_FAILED",
                        "Rebuilt CAD mesh validation failed: complete=" + targetMesh.isComplete()
                            + " num_elem=" + targetMesh.getNumElem()
                            + " min_quality=" + targetMesh.getMinQuality(),
                        ""
                    );
                    return;
                }
                geometryRebuilt = true;
            }

            MeshSequence activeMesh = comp.mesh(meshTag);
            String meshJson = "{\"tag\":" + ComsolBridge.quote(meshTag)
                + ",\"is_complete\":" + activeMesh.isComplete()
                + ",\"num_elem\":" + activeMesh.getNumElem()
                + ",\"num_vertex\":" + activeMesh.getNumVertex()
                + ",\"min_quality\":" + activeMesh.getMinQuality()
                + ",\"mean_quality\":" + activeMesh.getMeanQuality() + "}";

            double initialRpm = 0.0;
            try {
                initialRpm = model.param().evaluate("speed_rpm/1[rpm]");
            } catch (Throwable ignored) {}

            double targetRpm;
            if (spec.autoSpeed) {
                targetRpm = model.param().evaluate("speed_rpm/1[rpm]");
                if (!Double.isFinite(targetRpm) || targetRpm == 0.0) {
                    ComsolBridge.outputError("INVALID_PARAMETER", "Model speed_rpm evaluated to invalid value: " + targetRpm, "");
                    return;
                }
            } else {
                targetRpm = spec.speedRpm;
            }

            model.param().set("speed_rpm", Double.toString(targetRpm) + "[rpm]");
            double rpmReadback = model.param().evaluate("speed_rpm/1[rpm]");
            if (!Double.isFinite(rpmReadback) || Math.abs(rpmReadback - targetRpm) > 1e-6) {
                ComsolBridge.outputError("INVALID_UNIT", "speed_rpm readback mismatch: expected " + targetRpm + ", got " + rpmReadback, "");
                return;
            }

            // Determine spatial far-field boundaries using existing dset1 or Mesh dataset
            String preEvalDs = ensureEvalDataset(model, "c", meshTag, "sol1", geometryRebuilt);
            int[][] farSplit = classifyFarBoundaries(model, comp, preEvalDs);
            int[] farNegX = farSplit[0];
            int[] farPosX = farSplit[1];
            int[] farLateral = farSplit[2];

            int[] upstreamSel;
            int[] openSel;
            if (rpmReadback > 0.0) {
                // CW (+rpm): flow toward -X; upstream=+X cap ([29]), open=-X cap + lateral ([1,2,3])
                upstreamSel = farPosX;
                openSel = unionSorted(farNegX, farLateral);
            } else {
                // CCW (-rpm): flow toward +X; upstream=-X cap ([1]), open=lateral + +X cap ([2,3,29])
                upstreamSel = farNegX;
                openSel = unionSorted(farLateral, farPosX);
            }

            Physics spf = comp.physics("spf");
            if (!containsTag(spf.feature().tags(), "upstream") || !containsTag(spf.feature().tags(), "open")) {
                ComsolBridge.outputError("NODE_NOT_FOUND", "Physics BCs 'upstream' or 'open' not found in spf", "");
                return;
            }
            spf.feature("upstream").selection().set(upstreamSel);
            spf.feature("open").selection().set(openSel);

            int[] upstreamReadback = sortedCopy(spf.feature("upstream").selection().entities());
            int[] openReadback = sortedCopy(spf.feature("open").selection().entities());
            if (!Arrays.equals(upstreamReadback, upstreamSel) || !Arrays.equals(openReadback, openSel)) {
                ComsolBridge.outputError("MODEL_VALIDATION_FAILED", "BC selection readback mismatch after assignment", "");
                return;
            }

            if (!spec.solve) {
                String geomAuditJson = buildGeometryAuditJson(model, comp, preEvalDs);
                cleanupTempNodes(model);
                if (spec.saveOutput) {
                    model.save(outputPathOrDash);
                }
                StringBuilder gsb = new StringBuilder();
                gsb.append("{\n");
                gsb.append("  \"status\": \"SUCCESS\",\n");
                gsb.append("  \"operation\": ").append(ComsolBridge.quote(operation)).append(",\n");
                gsb.append("  \"model_path\": ").append(ComsolBridge.quote(modelPath)).append(",\n");
                gsb.append("  \"output_model\": ").append(spec.saveOutput ? ComsolBridge.quote(outputPathOrDash) : "null").append(",\n");
                gsb.append("  \"solved\": false,\n");
                gsb.append("  \"geometry_rebuilt\": ").append(geometryRebuilt).append(",\n");
                gsb.append("  \"min_mesh_quality\": ").append(activeMesh.getMinQuality()).append(",\n");
                gsb.append("  \"mean_mesh_quality\": ").append(activeMesh.getMeanQuality()).append(",\n");
                gsb.append("  \"mesh\": ").append(meshJson).append(",\n");
                gsb.append("  \"bc_selection\": {\n");
                gsb.append("    \"upstream\": ").append(ComsolBridge.intArrayToJson(upstreamReadback)).append(",\n");
                gsb.append("    \"open\": ").append(ComsolBridge.intArrayToJson(openReadback)).append("\n");
                gsb.append("  },\n");
                gsb.append("  \"geometry_audit\": ").append(geomAuditJson).append("\n");
                gsb.append("}\n");
                ComsolBridge.emitResult(gsb.toString());
                return;
            }

            // Configure solver sol1
            SolverSequence sol1 = model.sol("sol1");
            if (!containsTag(sol1.feature().tags(), "v1") || !containsTag(sol1.feature().tags(), "s1")) {
                ComsolBridge.outputError("NODE_NOT_FOUND", "Solver features v1 or s1 not found in sol1", "");
                return;
            }
            boolean seedAvailable = containsTag(model.sol().tags(), spec.initsol) && !model.sol(spec.initsol).isEmpty();
            boolean directionCompatible = (initialRpm == 0.0) || (initialRpm > 0 && targetRpm > 0) || (initialRpm < 0 && targetRpm < 0);
            boolean useSeed;
            if (geometryRebuilt || !directionCompatible || "init".equals(spec.warmStart)) {
                useSeed = false;
            } else if ("seed".equals(spec.warmStart)) {
                if (!seedAvailable) {
                    ComsolBridge.outputError("MODEL_VALIDATION_FAILED", "warm_start=seed requested but solution '" + spec.initsol + "' is missing or empty", "");
                    return;
                }
                useSeed = true;
            } else {
                useSeed = seedAvailable;
            }

            SolverFeature v1 = sol1.feature("v1");
            v1.set("control", "user");
            if (useSeed) {
                v1.set("initmethod", "sol");
                v1.set("initsol", spec.initsol);
                v1.set("initsoluse", "current");
                v1.set("solnum", "last");
            } else {
                v1.set("initmethod", "init");
                v1.set("initsol", "zero");
                v1.set("solnum", "auto");
                v1.set("notsolmethod", "init");
                v1.set("notsol", "zero");
            }

            SolverFeature s1 = sol1.feature("s1");
            s1.set("control", "user");
            s1.set("stol", Double.toString(spec.stol));
            if (containsTag(s1.feature().tags(), "se1")) {
                SolverFeature se1 = s1.feature("se1");
                se1.set("segcflaa", spec.segcflaa);
                se1.set("submincfl", spec.submincfl);
                se1.set("maxsegiter", spec.maxiter);
                if (containsTag(se1.feature().tags(), "ss1")) {
                    se1.feature("ss1").set("subdamp", spec.subdamp);
                }
                if (containsTag(se1.feature().tags(), "ss2")) {
                    se1.feature("ss2").set("subdamp", spec.subdamp);
                }
            }

            // Execute solve via sol1.runAll(false) (st1 -> v1 -> s1)
            long t0 = System.nanoTime();
            sol1.runAll(false);
            long elapsedNs = System.nanoTime() - t0;
            double wallTimeS = elapsedNs / 1e9;
            long elapsedMs = elapsedNs / 1_000_000L;

            // Post-compile verification of builder_integrate2 (open) and builder_integrate3 (upstream)
            int[] b2Ents = new int[0];
            int[] b3Ents = new int[0];
            if (containsTag(comp.cpl().tags(), "builder_integrate2")) {
                b2Ents = sortedCopy(comp.cpl("builder_integrate2").selection().entities());
                if (!Arrays.equals(b2Ents, openSel)) {
                    ComsolBridge.outputError(
                        "MODEL_VALIDATION_FAILED",
                        "Post-compile builder_integrate2 selection " + Arrays.toString(b2Ents) + " != open " + Arrays.toString(openSel),
                        ""
                    );
                    return;
                }
            }
            if (containsTag(comp.cpl().tags(), "builder_integrate3")) {
                b3Ents = sortedCopy(comp.cpl("builder_integrate3").selection().entities());
                if (!Arrays.equals(b3Ents, upstreamSel)) {
                    ComsolBridge.outputError(
                        "MODEL_VALIDATION_FAILED",
                        "Post-compile builder_integrate3 selection " + Arrays.toString(b3Ents) + " != upstream " + Arrays.toString(upstreamSel),
                        ""
                    );
                    return;
                }
            }

            if (sol1.isEmpty()) {
                ComsolBridge.outputError("SOLVER_FAILED", "sol1 is empty after runAll(false)", "");
                return;
            }
            double[] solU = sol1.getU(1);
            if (solU == null || solU.length == 0) {
                ComsolBridge.outputError("SOLVER_FAILED", "sol1.getU(1) returned empty vector", "");
                return;
            }
            for (double v : solU) {
                if (!Double.isFinite(v)) {
                    ComsolBridge.outputError("SOLVER_FAILED", "sol1 solution vector contains non-finite value", "");
                    return;
                }
            }

            String solverMsg = "";
            try { solverMsg = s1.getString("message"); } catch (Throwable ignored) {}
            double[] solverErrors = parseLastSolverErrors(solverMsg);
            int solverIters = parseSolverIterations(solverMsg);
            double maxSolverErr = Double.NaN;
            if (solverErrors.length > 0) {
                maxSolverErr = 0.0;
                for (double err : solverErrors) {
                    maxSolverErr = Math.max(maxSolverErr, err);
                }
            }
            boolean converged = solverErrors.length > 0 && Double.isFinite(maxSolverErr) && maxSolverErr <= spec.stol;
            if (spec.strictConvergence && !converged) {
                ComsolBridge.outputError(
                    "SOLVER_FAILED",
                    "Solver did not meet relative tolerance stol=" + spec.stol + " (parsed solver_err=" + Arrays.toString(solverErrors) + ")",
                    solverMsg != null ? solverMsg : ""
                );
                return;
            }

            // Ensure clean numerical evaluation on solved dset1
            cleanupTempNodes(model);
            String evalDs = "dset1";

            // 1. Summary EvalGlobal with explicit units
            NumericalFeature sumEval = model.result().numerical().create("tmp_prop_summary", "EvalGlobal");
            sumEval.set("data", evalDs);
            sumEval.set("expr", new String[]{
                "-intR(spf.T_tracx)",
                "-intD(spf.T_tracx)",
                "-intR(spf.T_tracx)-intD(spf.T_tracx)",
                "intR(y*spf.T_tracz-z*spf.T_tracy)",
                "2*pi*speed_rpm*intR(y*spf.T_tracz-z*spf.T_tracy)"
            });
            sumEval.set("unit", new String[]{"N", "N", "N", "N*m", "kW"});
            double[][] sumVals = sumEval.getReal();
            double fRotorX = sumVals[0][0];
            double fDuctX = sumVals[1][0];
            double fTotalX = sumVals[2][0];
            double driveMx = sumVals[3][0];
            double shaftPowerKw = sumVals[4][0];
            double thrustN = Math.abs(fTotalX);
            double thrustPerPower = Math.abs(shaftPowerKw) > 1e-12 ? (thrustN / shaftPowerKw) : 0.0;

            // 1b. Pressure vs Viscous force & torque split on impeller and nozzle
            NumericalFeature splitImp = model.result().numerical().create("tmp_prop_split_imp", "IntSurface");
            splitImp.set("data", evalDs);
            splitImp.selection().named("impeller");
            splitImp.set("expr", new String[]{
                "-spf.T_tracx",
                "p*spf.nxmesh",
                "-spf.T_tracx-p*spf.nxmesh",
                "y*spf.T_tracz-z*spf.T_tracy",
                "-p*(y*spf.nzmesh-z*spf.nymesh)",
                "(y*spf.T_tracz-z*spf.T_tracy)+p*(y*spf.nzmesh-z*spf.nymesh)"
            });
            double[][] splitImpVals = splitImp.getReal();
            double fRotorPressureX = splitImpVals[1][0];
            double fRotorViscousX = splitImpVals[2][0];
            double driveMxPressure = splitImpVals[4][0];
            double driveMxViscous = splitImpVals[5][0];

            NumericalFeature splitNoz = model.result().numerical().create("tmp_prop_split_noz", "IntSurface");
            splitNoz.set("data", evalDs);
            splitNoz.selection().named("nozzle");
            splitNoz.set("expr", new String[]{
                "-spf.T_tracx",
                "p*spf.nxmesh",
                "-spf.T_tracx-p*spf.nxmesh"
            });
            double[][] splitNozVals = splitNoz.getReal();
            double fDuctPressureX = splitNozVals[1][0];
            double fDuctViscousX = splitNozVals[2][0];

            // 2. Far-field mass & momentum flux closure (explicit kg/s and m^3/s)
            NumericalFeature farEval = model.result().numerical().create("tmp_prop_far", "IntSurface");
            farEval.set("data", evalDs);
            farEval.selection().named("far");
            farEval.set("expr", new String[]{
                "rho_water*(u*nx+v*ny+w*nz)",
                "rho_water*abs((u*nx+v*ny+w*nz))",
                "-rho_water*u*(u*nx+v*ny+w*nz)",
                "-p*nx",
                "u*nx+v*ny+w*nz",
                "abs(u*nx+v*ny+w*nz)"
            });
            double[][] farVals = farEval.getReal();
            double netMassKgS = farVals[0][0];
            double grossMassKgS = farVals[1][0];
            double momX = farVals[2][0];
            double pReactX = farVals[3][0];
            double netVolM3S = farVals[4][0];
            double grossVolM3S = farVals[5][0];

            // Plane mass flux & axial velocity (planes selection)
            double velPlaneNegX = Double.NaN;
            double mdotPlaneNegX = Double.NaN;
            double velPlanePosX = Double.NaN;
            double mdotPlanePosX = Double.NaN;
            if (containsTag(comp.selection().tags(), "planes")) {
                int[] pEnts = sortedCopy(comp.selection("planes").entities());
                if (pEnts.length >= 2) {
                    int negPlane = pEnts[0];
                    int posPlane = pEnts[1];
                    double[] p0mm = evalSurfaceMinMax(model, evalDs, null, new int[]{negPlane}, new String[]{"x", "y", "z", "sqrt(y^2+z^2)"});
                    double[] p1mm = evalSurfaceMinMax(model, evalDs, null, new int[]{posPlane}, new String[]{"x", "y", "z", "sqrt(y^2+z^2)"});
                    if (p1mm[0] < p0mm[0]) {
                        int tmp = negPlane;
                        negPlane = posPlane;
                        posPlane = tmp;
                    }
                    NumericalFeature avNeg = model.result().numerical().create("tmp_prop_avp0", "AvSurface");
                    avNeg.set("data", evalDs);
                    avNeg.selection().set(new int[]{negPlane});
                    avNeg.set("expr", new String[]{"u"});
                    velPlaneNegX = avNeg.getReal()[0][0];

                    NumericalFeature intNeg = model.result().numerical().create("tmp_prop_intp0", "IntSurface");
                    intNeg.set("data", evalDs);
                    intNeg.selection().set(new int[]{negPlane});
                    intNeg.set("expr", new String[]{"rho_water*u"});
                    mdotPlaneNegX = intNeg.getReal()[0][0];

                    NumericalFeature avPos = model.result().numerical().create("tmp_prop_avp1", "AvSurface");
                    avPos.set("data", evalDs);
                    avPos.selection().set(new int[]{posPlane});
                    avPos.set("expr", new String[]{"u"});
                    velPlanePosX = avPos.getReal()[0][0];

                    NumericalFeature intPos = model.result().numerical().create("tmp_prop_intp1", "IntSurface");
                    intPos.set("data", evalDs);
                    intPos.selection().set(new int[]{posPlane});
                    intPos.set("expr", new String[]{"rho_water*u"});
                    mdotPlanePosX = intPos.getReal()[0][0];
                }
            }

            double massImbGrossPct = grossMassKgS > 1e-12 ? (100.0 * Math.abs(netMassKgS) / grossMassKgS) : Double.NaN;
            double refPlaneMdot = Double.isFinite(mdotPlaneNegX) ? Math.abs(mdotPlaneNegX) : grossMassKgS;
            double massImbPlanePct = refPlaneMdot > 1e-12 ? (100.0 * Math.abs(netMassKgS) / refPlaneMdot) : Double.NaN;
            double momClosurePct = Math.abs(fTotalX) > 1e-12
                ? (100.0 * Math.abs((momX + pReactX) - fTotalX) / Math.abs(fTotalX))
                : Double.NaN;

            // 3. Cavitation & multi-band negative pressure distribution on impeller and nozzle
            NumericalFeature cavEval = model.result().numerical().create("tmp_prop_cav", "IntSurface");
            cavEval.set("data", evalDs);
            cavEval.selection().named("impeller");
            cavEval.set("expr", new String[]{
                "1",
                "if(p<0[kPa],1,0)",
                "if(p<-25[kPa],1,0)",
                "if(p<-50[kPa],1,0)",
                "if(p<-75[kPa],1,0)",
                "if(p<-99[kPa],1,0)",
                "if(p<-125[kPa],1,0)",
                "if(p<-150[kPa],1,0)",
                "if(p<-99[kPa] && sqrt(y^2+z^2)<0.08[m],1,0)",
                "if(p<-99[kPa] && sqrt(y^2+z^2)>=0.08[m] && sqrt(y^2+z^2)<0.12[m],1,0)",
                "if(p<-99[kPa] && sqrt(y^2+z^2)>=0.12[m],1,0)"
            });
            double[][] cavVals = cavEval.getReal();
            double impAreaM2 = cavVals[0][0];
            double cavArea0M2 = cavVals[1][0];
            double cavArea25M2 = cavVals[2][0];
            double cavArea50M2 = cavVals[3][0];
            double cavArea75M2 = cavVals[4][0];
            double cavArea99M2 = cavVals[5][0];
            double cavArea125M2 = cavVals[6][0];
            double cavArea150M2 = cavVals[7][0];
            double cavArea99RootM2 = cavVals[8][0];
            double cavArea99MidM2 = cavVals[9][0];
            double cavArea99TipM2 = cavVals[10][0];
            double cavArea0Pct = impAreaM2 > 1e-12 ? (100.0 * cavArea0M2 / impAreaM2) : 0.0;
            double cavArea25Pct = impAreaM2 > 1e-12 ? (100.0 * cavArea25M2 / impAreaM2) : 0.0;
            double cavArea50Pct = impAreaM2 > 1e-12 ? (100.0 * cavArea50M2 / impAreaM2) : 0.0;
            double cavArea75Pct = impAreaM2 > 1e-12 ? (100.0 * cavArea75M2 / impAreaM2) : 0.0;
            double cavArea99Pct = impAreaM2 > 1e-12 ? (100.0 * cavArea99M2 / impAreaM2) : 0.0;
            double cavArea125Pct = impAreaM2 > 1e-12 ? (100.0 * cavArea125M2 / impAreaM2) : 0.0;
            double cavArea150Pct = impAreaM2 > 1e-12 ? (100.0 * cavArea150M2 / impAreaM2) : 0.0;

            NumericalFeature pminEval = model.result().numerical().create("tmp_prop_pmin", "MinSurface");
            pminEval.set("data", evalDs);
            pminEval.selection().named("impeller");
            pminEval.set("expr", new String[]{"p"});
            double pMinImpPa = pminEval.getReal()[0][0];

            NumericalFeature pmaxEval = model.result().numerical().create("tmp_prop_pmax", "MaxSurface");
            pmaxEval.set("data", evalDs);
            pmaxEval.selection().named("impeller");
            pmaxEval.set("expr", new String[]{"p"});
            double pMaxImpPa = pmaxEval.getReal()[0][0];

            NumericalFeature nozCavEval = model.result().numerical().create("tmp_prop_nozcav", "IntSurface");
            nozCavEval.set("data", evalDs);
            nozCavEval.selection().named("nozzle");
            nozCavEval.set("expr", new String[]{"1", "if(p<0[kPa],1,0)", "if(p<-99[kPa],1,0)"});
            double[][] nozCavVals = nozCavEval.getReal();
            double nozAreaM2 = nozCavVals[0][0];
            double nozNeg0M2 = nozCavVals[1][0];
            double nozNeg99M2 = nozCavVals[2][0];

            NumericalFeature pminNozEval = model.result().numerical().create("tmp_prop_pmin_noz", "MinSurface");
            pminNozEval.set("data", evalDs);
            pminNozEval.selection().named("nozzle");
            pminNozEval.set("expr", new String[]{"p"});
            double pMinNozPa = pminNozEval.getReal()[0][0];

            NumericalFeature pmaxNozEval = model.result().numerical().create("tmp_prop_pmax_noz", "MaxSurface");
            pmaxNozEval.set("data", evalDs);
            pmaxNozEval.selection().named("nozzle");
            pmaxNozEval.set("expr", new String[]{"p"});
            double pMaxNozPa = pmaxNozEval.getReal()[0][0];

            // 4. Wall y+
            NumericalFeature ypImpEval = model.result().numerical().create("tmp_prop_yp_imp", "AvSurface");
            ypImpEval.set("data", evalDs);
            ypImpEval.selection().named("impeller");
            ypImpEval.set("expr", new String[]{"spf.d_w_plus"});
            double yPlusImp = ypImpEval.getReal()[0][0];

            NumericalFeature ypDuctEval = model.result().numerical().create("tmp_prop_yp_duct", "AvSurface");
            ypDuctEval.set("data", evalDs);
            ypDuctEval.selection().named("nozzle");
            ypDuctEval.set("expr", new String[]{"spf.d_w_plus"});
            double yPlusDuct = ypDuctEval.getReal()[0][0];

            // 5. Geometry audit
            String geomAuditJson = buildGeometryAuditJson(model, comp, evalDs);

            String direction = rpmReadback > 0.0 ? "cw" : "ccw";

            // 6. Optional COMSOL-native surface pressure & traction CSV exports
            String exportedImpCsv = null;
            String exportedNozCsv = null;
            if (!spec.exportSurfaceDir.isEmpty()) {
                String expDir = spec.exportSurfaceDir.replace('\\', '/').trim();
                if (!expDir.endsWith("/")) {
                    expDir += "/";
                }
                String impFile = expDir + direction + "_impeller_surface_pressure.csv";
                String nozFile = expDir + direction + "_nozzle_surface_pressure.csv";

                DatasetFeature impSds = model.result().dataset().create("tmp_prop_surf_imp", "Surface");
                impSds.set("data", evalDs);
                impSds.selection().named("impeller");
                ExportFeature impExp = model.result().export().create("tmp_prop_exp_imp", "Data");
                impExp.set("data", "tmp_prop_surf_imp");
                impExp.set("expr", new String[]{"p", "spf.T_tracx", "spf.T_tracy", "spf.T_tracz", "spf.d_w_plus"});
                impExp.set("filename", impFile);
                impExp.set("header", "on");
                impExp.set("fullprec", "on");
                impExp.set("includecoords", true);
                impExp.run();
                exportedImpCsv = impFile;

                DatasetFeature nozSds = model.result().dataset().create("tmp_prop_surf_noz", "Surface");
                nozSds.set("data", evalDs);
                nozSds.selection().named("nozzle");
                ExportFeature nozExp = model.result().export().create("tmp_prop_exp_noz", "Data");
                nozExp.set("data", "tmp_prop_surf_noz");
                nozExp.set("expr", new String[]{"p", "spf.T_tracx", "spf.T_tracy", "spf.T_tracz", "spf.d_w_plus"});
                nozExp.set("filename", nozFile);
                nozExp.set("header", "on");
                nozExp.set("fullprec", "on");
                nozExp.set("includecoords", true);
                nozExp.run();
                exportedNozCsv = nozFile;
            }

            // Remove all tmp_prop_* nodes BEFORE optional model.save()
            cleanupTempNodes(model);

            if (spec.saveOutput) {
                model.save(outputPathOrDash);
            }

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": ").append(ComsolBridge.quote(operation)).append(",\n");
            sb.append("  \"model_path\": ").append(ComsolBridge.quote(modelPath)).append(",\n");
            sb.append("  \"output_model\": ").append(spec.saveOutput ? ComsolBridge.quote(outputPathOrDash) : "null").append(",\n");
            sb.append("  \"study_tag\": \"std\",\n");
            sb.append("  \"solver_tag\": \"sol1\",\n");
            sb.append("  \"speed_rpm\": ").append(rpmReadback).append(",\n");
            sb.append("  \"direction\": ").append(ComsolBridge.quote(direction)).append(",\n");
            sb.append("  \"elapsed_ms\": ").append(elapsedMs).append(",\n");
            sb.append("  \"wall_time_s\": ").append(wallTimeS).append(",\n");
            sb.append("  \"geometry_rebuilt\": ").append(geometryRebuilt).append(",\n");
            sb.append("  \"mesh\": ").append(meshJson).append(",\n");
            sb.append("  \"surface_exports\": {\n");
            sb.append("    \"impeller_csv\": ").append(exportedImpCsv != null ? ComsolBridge.quote(exportedImpCsv) : "null").append(",\n");
            sb.append("    \"nozzle_csv\": ").append(exportedNozCsv != null ? ComsolBridge.quote(exportedNozCsv) : "null").append("\n");
            sb.append("  },\n");
            sb.append("  \"bc_selection\": {\n");
            sb.append("    \"upstream\": ").append(ComsolBridge.intArrayToJson(upstreamReadback)).append(",\n");
            sb.append("    \"open\": ").append(ComsolBridge.intArrayToJson(openReadback)).append(",\n");
            sb.append("    \"builder_integrate2_open\": ").append(ComsolBridge.intArrayToJson(b2Ents)).append(",\n");
            sb.append("    \"builder_integrate3_upstream\": ").append(ComsolBridge.intArrayToJson(b3Ents)).append("\n");
            sb.append("  },\n");
            sb.append("  \"kpis\": {\n");
            sb.append("    \"F_rotor_x_N\": ").append(fRotorX).append(",\n");
            sb.append("    \"F_rotor_pressure_x_N\": ").append(fRotorPressureX).append(",\n");
            sb.append("    \"F_rotor_viscous_x_N\": ").append(fRotorViscousX).append(",\n");
            sb.append("    \"F_duct_x_N\": ").append(fDuctX).append(",\n");
            sb.append("    \"F_duct_pressure_x_N\": ").append(fDuctPressureX).append(",\n");
            sb.append("    \"F_duct_viscous_x_N\": ").append(fDuctViscousX).append(",\n");
            sb.append("    \"F_total_x_N\": ").append(fTotalX).append(",\n");
            sb.append("    \"thrust_N\": ").append(thrustN).append(",\n");
            sb.append("    \"drive_torque_x_Nm\": ").append(driveMx).append(",\n");
            sb.append("    \"drive_torque_pressure_x_Nm\": ").append(driveMxPressure).append(",\n");
            sb.append("    \"drive_torque_viscous_x_Nm\": ").append(driveMxViscous).append(",\n");
            sb.append("    \"shaft_power_kW\": ").append(shaftPowerKw).append(",\n");
            sb.append("    \"thrust_per_power_N_per_kW\": ").append(thrustPerPower).append(",\n");
            sb.append("    \"y_plus_imp\": ").append(yPlusImp).append(",\n");
            sb.append("    \"y_plus_duct\": ").append(yPlusDuct).append("\n");
            sb.append("  },\n");
            sb.append("  \"flow_audit\": {\n");
            sb.append("    \"net_mass_flux_kg_s\": ").append(netMassKgS).append(",\n");
            sb.append("    \"gross_mass_flux_kg_s\": ").append(grossMassKgS).append(",\n");
            sb.append("    \"net_vol_flux_m3_s\": ").append(netVolM3S).append(",\n");
            sb.append("    \"gross_vol_flux_m3_s\": ").append(grossVolM3S).append(",\n");
            sb.append("    \"momentum_x_N\": ").append(momX).append(",\n");
            sb.append("    \"pressure_reaction_x_N\": ").append(pReactX).append(",\n");
            sb.append("    \"vel_plane_neg_x_m_s\": ").append(Double.isFinite(velPlaneNegX) ? Double.toString(velPlaneNegX) : "null").append(",\n");
            sb.append("    \"mdot_plane_neg_x_kg_s\": ").append(Double.isFinite(mdotPlaneNegX) ? Double.toString(mdotPlaneNegX) : "null").append(",\n");
            sb.append("    \"vel_plane_pos_x_m_s\": ").append(Double.isFinite(velPlanePosX) ? Double.toString(velPlanePosX) : "null").append(",\n");
            sb.append("    \"mdot_plane_pos_x_kg_s\": ").append(Double.isFinite(mdotPlanePosX) ? Double.toString(mdotPlanePosX) : "null").append(",\n");
            sb.append("    \"mass_imbalance_gross_pct\": ").append(Double.isFinite(massImbGrossPct) ? Double.toString(massImbGrossPct) : "null").append(",\n");
            sb.append("    \"mass_imbalance_plane_pct\": ").append(Double.isFinite(massImbPlanePct) ? Double.toString(massImbPlanePct) : "null").append(",\n");
            sb.append("    \"momentum_closure_pct\": ").append(Double.isFinite(momClosurePct) ? Double.toString(momClosurePct) : "null").append("\n");
            sb.append("  },\n");
            sb.append("  \"cavitation\": {\n");
            sb.append("    \"p_min_imp_Pa\": ").append(pMinImpPa).append(",\n");
            sb.append("    \"p_max_imp_Pa\": ").append(pMaxImpPa).append(",\n");
            sb.append("    \"impeller_area_m2\": ").append(impAreaM2).append(",\n");
            sb.append("    \"cav_area_0_m2\": ").append(cavArea0M2).append(",\n");
            sb.append("    \"cav_area_25_m2\": ").append(cavArea25M2).append(",\n");
            sb.append("    \"cav_area_50_m2\": ").append(cavArea50M2).append(",\n");
            sb.append("    \"cav_area_75_m2\": ").append(cavArea75M2).append(",\n");
            sb.append("    \"cav_area_99_m2\": ").append(cavArea99M2).append(",\n");
            sb.append("    \"cav_area_125_m2\": ").append(cavArea125M2).append(",\n");
            sb.append("    \"cav_area_150_m2\": ").append(cavArea150M2).append(",\n");
            sb.append("    \"cav_area_0_pct\": ").append(cavArea0Pct).append(",\n");
            sb.append("    \"cav_area_25_pct\": ").append(cavArea25Pct).append(",\n");
            sb.append("    \"cav_area_50_pct\": ").append(cavArea50Pct).append(",\n");
            sb.append("    \"cav_area_75_pct\": ").append(cavArea75Pct).append(",\n");
            sb.append("    \"cav_area_99_pct\": ").append(cavArea99Pct).append(",\n");
            sb.append("    \"cav_area_125_pct\": ").append(cavArea125Pct).append(",\n");
            sb.append("    \"cav_area_150_pct\": ").append(cavArea150Pct).append(",\n");
            sb.append("    \"cav_area_99_root_r_lt_080_m2\": ").append(cavArea99RootM2).append(",\n");
            sb.append("    \"cav_area_99_mid_080_120_m2\": ").append(cavArea99MidM2).append(",\n");
            sb.append("    \"cav_area_99_tip_r_ge_120_m2\": ").append(cavArea99TipM2).append(",\n");
            sb.append("    \"p_min_nozzle_Pa\": ").append(pMinNozPa).append(",\n");
            sb.append("    \"p_max_nozzle_Pa\": ").append(pMaxNozPa).append(",\n");
            sb.append("    \"nozzle_area_m2\": ").append(nozAreaM2).append(",\n");
            sb.append("    \"nozzle_area_p_lt_0_m2\": ").append(nozNeg0M2).append(",\n");
            sb.append("    \"nozzle_area_p_lt_99kPa_m2\": ").append(nozNeg99M2).append("\n");
            sb.append("  },\n");
            sb.append("  \"solver_stats\": {\n");
            sb.append("    \"control\": ").append(ComsolBridge.quote(readSolverProp(s1, "control"))).append(",\n");
            sb.append("    \"stol\": ").append(spec.stol).append(",\n");
            sb.append("    \"segcflaa\": ").append(ComsolBridge.quote(spec.segcflaa)).append(",\n");
            sb.append("    \"submincfl\": ").append(spec.submincfl).append(",\n");
            sb.append("    \"subdamp\": ").append(spec.subdamp).append(",\n");
            sb.append("    \"maxiter\": ").append(spec.maxiter).append(",\n");
            sb.append("    \"use_seed\": ").append(useSeed).append(",\n");
            sb.append("    \"initsol\": ").append(ComsolBridge.quote(useSeed ? spec.initsol : "")).append(",\n");
            sb.append("    \"iterations\": ").append(solverIters).append(",\n");
            sb.append("    \"solver_err\": ").append(ComsolBridge.doubleArrayToJson(solverErrors)).append(",\n");
            sb.append("    \"max_solver_err\": ").append(Double.isFinite(maxSolverErr) ? Double.toString(maxSolverErr) : "null").append(",\n");
            sb.append("    \"converged\": ").append(converged).append(",\n");
            sb.append("    \"dof_count\": ").append(solU.length).append(",\n");
            sb.append("    \"solver_message\": ").append(ComsolBridge.quote(solverMsg != null ? solverMsg : "")).append("\n");
            sb.append("  },\n");
            sb.append("  \"geometry_audit\": ").append(geomAuditJson).append("\n");
            sb.append("}\n");

            ComsolBridge.emitResult(sb.toString());
        } catch (Throwable t) {
            ComsolBridge.outputError(
                "SOLVER_FAILED",
                "Propeller evaluation failed: " + t.getMessage(),
                ComsolBridge.getStackTrace(t)
            );
        } finally {
            if (model != null) {
                try { cleanupTempNodes(model); } catch (Throwable ignored) {}
            }
            try { ModelUtil.clear(); } catch (Throwable ignored) {}
        }
    }
}
