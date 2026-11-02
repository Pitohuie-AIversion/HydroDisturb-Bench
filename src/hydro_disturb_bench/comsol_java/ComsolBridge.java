import com.comsol.model.*;
import com.comsol.model.util.*;
import java.io.StringWriter;
import java.io.PrintWriter;
import java.util.Arrays;

/**
 * HydroDisturb-Bench COMSOL Java API Bridge
 *
 * Provides inspect_model, inspect_api, update_parameter, and run_study
 * directly via COMSOL official Java API.
 * Uses positional arguments to avoid any conflict with comsolbatch flag parser.
 */
public class ComsolBridge {

    private static final String JSON_START = "<<<JSON_START>>>";
    private static final String JSON_END = "<<<JSON_END>>>";

    public static void main(String[] args) {
        if (args == null || args.length == 0) {
            outputError("API_UNSUPPORTED", "No command provided", "");
            return;
        }

        String command = args[0];

        try {
            if ("inspect-api".equals(command)) {
                handleInspectApi();
            } else if ("inspect-model".equals(command)) {
                if (args.length < 2) {
                    outputError("INVALID_PARAMETER", "Missing model path for inspect-model", "");
                    return;
                }
                handleInspectModel(args[1]);
            } else if ("update-param".equals(command)) {
                if (args.length < 5) {
                    outputError("INVALID_PARAMETER", "Usage: update-param <modelPath> <outputPath> <paramName> <paramExpr> [paramDescr]", "");
                    return;
                }
                String descr = (args.length >= 6) ? args[5] : "";
                handleUpdateParam(args[1], args[2], args[3], args[4], descr);
            } else if ("run-study".equals(command)) {
                if (args.length < 4) {
                    outputError("INVALID_PARAMETER", "Usage: run-study <modelPath> <outputPath> <studyTag> [tlistOverride]", "");
                    return;
                }
                String tlist = (args.length >= 5) ? args[4] : "";
                handleRunStudy(args[1], args[2], args[3], tlist);
            } else {
                outputError("API_UNSUPPORTED", "Unknown command: " + command, "");
            }
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            outputError("COMSOL_CONNECTION_FAILED", "Unexpected exception: " + t.getMessage(), sw.toString());
        }
    }

    private static void handleInspectApi() {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": \"SUCCESS\",\n");
        sb.append("  \"operation\": \"inspect_api\",\n");
        sb.append("  \"comsol_version\": ").append(quote(ModelUtil.getComsolVersion())).append(",\n");
        sb.append("  \"api_specification\": {\n");
        sb.append("    \"entry_point\": \"com.comsol.model.util.ModelUtil\",\n");
        sb.append("    \"supported_methods\": [\n");
        sb.append("      {\"method\": \"ModelUtil.loadCopy(tag, path)\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (ModelUtil)\"},\n");
        sb.append("      {\"method\": \"ModelUtil.getComsolVersion()\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (ModelUtil)\"},\n");
        sb.append("      {\"method\": \"model.param().varnames()\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (Parameters)\"},\n");
        sb.append("      {\"method\": \"model.param().get(name)\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (Parameters)\"},\n");
        sb.append("      {\"method\": \"model.param().set(name, expr)\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (Parameters)\"},\n");
        sb.append("      {\"method\": \"model.param().evaluate(name)\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (Parameters)\"},\n");
        sb.append("      {\"method\": \"model.param().evaluateUnit(name)\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (Parameters)\"},\n");
        sb.append("      {\"method\": \"model.component().tags()\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (ModelNode)\"},\n");
        sb.append("      {\"method\": \"model.study(tag).run()\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (Study)\"},\n");
        sb.append("      {\"method\": \"model.save(path)\", \"doc\": \"COMSOL Programming Reference Manual Chapter 2 (Model)\"}\n");
        sb.append("    ]\n");
        sb.append("  }\n");
        sb.append("}\n");
        emitResult(sb.toString());
    }

    private static void handleInspectModel(String modelPath) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            String msg = t.getMessage() != null ? t.getMessage() : "";
            if (msg.toLowerCase().contains("not found") || msg.toLowerCase().contains("no such file") || msg.toLowerCase().contains("cannot find")) {
                outputError("MODEL_NOT_FOUND", "Target model file not found: " + modelPath, getStackTrace(t));
            } else {
                outputError("COMSOL_CONNECTION_FAILED", "Failed to load model: " + msg, getStackTrace(t));
            }
            return;
        }

        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": \"SUCCESS\",\n");
        sb.append("  \"operation\": \"inspect_model\",\n");
        sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
        sb.append("  \"model_tag\": ").append(quote(model.tag())).append(",\n");
        sb.append("  \"comsol_version\": ").append(quote(ModelUtil.getComsolVersion())).append(",\n");

        // Parameters
        sb.append("  \"parameters\": {\n");
        ModelParam param = model.param();
        String[] varnames = param.varnames();
        for (int i = 0; i < varnames.length; i++) {
            String v = varnames[i];
            String expr = param.get(v);
            Double val = null;
            try { val = param.evaluate(v); } catch (Exception ignored) {}
            String unit = "";
            try { unit = param.evaluateUnit(v); } catch (Exception ignored) {}
            String desc = param.descr(v);

            sb.append("    ").append(quote(v)).append(": {\n");
            sb.append("      \"expr\": ").append(quote(expr)).append(",\n");
            sb.append("      \"value\": ").append(val == null ? "null" : val.toString()).append(",\n");
            sb.append("      \"unit\": ").append(quote(unit)).append(",\n");
            sb.append("      \"description\": ").append(quote(desc != null ? desc : "")).append("\n");
            sb.append("    }").append(i < varnames.length - 1 ? "," : "").append("\n");
        }
        sb.append("  },\n");

        // Components
        sb.append("  \"components\": [\n");
        String[] ctags = model.component().tags();
        for (int i = 0; i < ctags.length; i++) {
            String ctag = ctags[i];
            ModelNode comp = model.component(ctag);
            sb.append("    {\n");
            sb.append("      \"tag\": ").append(quote(ctag)).append(",\n");
            sb.append("      \"label\": ").append(quote(comp.label())).append(",\n");

            // Geometries
            sb.append("      \"geometries\": [\n");
            String[] gtags = comp.geom().tags();
            for (int j = 0; j < gtags.length; j++) {
                String gtag = gtags[j];
                GeomSequence geom = comp.geom(gtag);
                sb.append("        {\n");
                sb.append("          \"tag\": ").append(quote(gtag)).append(",\n");
                sb.append("          \"sdim\": ").append(geom.getSDim()).append(",\n");
                sb.append("          \"features\": ").append(stringArrayToJson(geom.feature().tags())).append("\n");
                sb.append("        }").append(j < gtags.length - 1 ? "," : "").append("\n");
            }
            sb.append("      ],\n");

            // Meshes
            sb.append("      \"meshes\": [\n");
            String[] mtags = comp.mesh().tags();
            for (int j = 0; j < mtags.length; j++) {
                String mtag = mtags[j];
                MeshSequence mesh = comp.mesh(mtag);
                sb.append("        {\n");
                sb.append("          \"tag\": ").append(quote(mtag)).append(",\n");
                sb.append("          \"sdim\": ").append(mesh.getSDim()).append(",\n");
                sb.append("          \"is_complete\": ").append(mesh.isComplete()).append(",\n");
                sb.append("          \"num_elem\": ").append(mesh.getNumElem()).append(",\n");
                sb.append("          \"num_vertex\": ").append(mesh.getNumVertex()).append(",\n");
                sb.append("          \"features\": ").append(stringArrayToJson(mesh.feature().tags())).append("\n");
                sb.append("        }").append(j < mtags.length - 1 ? "," : "").append("\n");
            }
            sb.append("      ],\n");

            // Physics
            sb.append("      \"physics\": [\n");
            String[] ptags = comp.physics().tags();
            for (int j = 0; j < ptags.length; j++) {
                String ptag = ptags[j];
                Object phys = comp.physics(ptag);
                String plabel = invokeStringMethod(phys, "label");
                String ptype = invokeStringMethod(phys, "getType");

                sb.append("        {\n");
                sb.append("          \"tag\": ").append(quote(ptag)).append(",\n");
                sb.append("          \"label\": ").append(quote(plabel)).append(",\n");
                sb.append("          \"type\": ").append(quote(ptype)).append(",\n");
                sb.append("          \"features\": [\n");

                try {
                    Object featList = phys.getClass().getMethod("feature").invoke(phys);
                    String[] ftags = (String[]) featList.getClass().getMethod("tags").invoke(featList);
                    for (int k = 0; k < ftags.length; k++) {
                        String ftag = ftags[k];
                        Object feat = phys.getClass().getMethod("feature", String.class).invoke(phys, ftag);
                        String flabel = invokeStringMethod(feat, "label");
                        String ftype = invokeStringMethod(feat, "getType");
                        int[] entities = new int[0];
                        try {
                            Object sel = feat.getClass().getMethod("selection").invoke(feat);
                            entities = (int[]) sel.getClass().getMethod("entities").invoke(sel);
                        } catch (Exception ignored) {}

                        sb.append("            {\n");
                        sb.append("              \"tag\": ").append(quote(ftag)).append(",\n");
                        sb.append("              \"label\": ").append(quote(flabel)).append(",\n");
                        sb.append("              \"type\": ").append(quote(ftype)).append(",\n");
                        sb.append("              \"selection_entities\": ").append(intArrayToJson(entities)).append("\n");
                        sb.append("            }").append(k < ftags.length - 1 ? "," : "").append("\n");
                    }
                } catch (Exception e) {
                    // fallback
                }
                sb.append("          ]\n");
                sb.append("        }").append(j < ptags.length - 1 ? "," : "").append("\n");
            }
            sb.append("      ],\n");

            // Materials
            sb.append("      \"materials\": [\n");
            String[] matTags = comp.material().tags();
            for (int j = 0; j < matTags.length; j++) {
                String matTag = matTags[j];
                String mlabel = comp.material(matTag).label();
                sb.append("        {\n");
                sb.append("          \"tag\": ").append(quote(matTag)).append(",\n");
                sb.append("          \"label\": ").append(quote(mlabel)).append("\n");
                sb.append("        }").append(j < matTags.length - 1 ? "," : "").append("\n");
            }
            sb.append("      ]\n");

            sb.append("    }").append(i < ctags.length - 1 ? "," : "").append("\n");
        }
        sb.append("  ],\n");

        // Studies
        sb.append("  \"studies\": [\n");
        String[] stags = model.study().tags();
        for (int i = 0; i < stags.length; i++) {
            String stag = stags[i];
            Study study = model.study(stag);
            sb.append("    {\n");
            sb.append("      \"tag\": ").append(quote(stag)).append(",\n");
            sb.append("      \"label\": ").append(quote(study.label())).append(",\n");
            sb.append("      \"features\": [\n");
            String[] ftags = study.feature().tags();
            for (int j = 0; j < ftags.length; j++) {
                String ftag = ftags[j];
                StudyFeature sfeat = study.feature(ftag);
                sb.append("        {\n");
                sb.append("          \"tag\": ").append(quote(ftag)).append(",\n");
                sb.append("          \"label\": ").append(quote(sfeat.label())).append(",\n");
                sb.append("          \"type\": ").append(quote(sfeat.getType())).append("\n");
                sb.append("        }").append(j < ftags.length - 1 ? "," : "").append("\n");
            }
            sb.append("      ]\n");
            sb.append("    }").append(i < stags.length - 1 ? "," : "").append("\n");
        }
        sb.append("  ],\n");

        // Solvers
        sb.append("  \"solvers\": [\n");
        String[] solTags = model.sol().tags();
        for (int i = 0; i < solTags.length; i++) {
            String solTag = solTags[i];
            SolverSequence sol = model.sol(solTag);
            sb.append("    {\n");
            sb.append("      \"tag\": ").append(quote(solTag)).append(",\n");
            sb.append("      \"label\": ").append(quote(sol.label())).append(",\n");
            sb.append("      \"features\": ").append(stringArrayToJson(sol.feature().tags())).append("\n");
            sb.append("    }").append(i < solTags.length - 1 ? "," : "").append("\n");
        }
        sb.append("  ]\n");
        sb.append("}\n");

        emitResult(sb.toString());
    }

    private static void handleUpdateParam(String modelPath, String outputPath, String paramName, String paramExpr, String descr) {
        if (modelPath.equalsIgnoreCase(outputPath)) {
            outputError("MODEL_VALIDATION_FAILED", "Refusing to overwrite baseline model: source and output path are identical", "");
            return;
        }

        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            String msg = t.getMessage() != null ? t.getMessage() : "";
            if (msg.toLowerCase().contains("not found") || msg.toLowerCase().contains("no such file") || msg.toLowerCase().contains("cannot find")) {
                outputError("MODEL_NOT_FOUND", "Source model file not found: " + modelPath, getStackTrace(t));
            } else {
                outputError("COMSOL_CONNECTION_FAILED", "Failed to load model: " + msg, getStackTrace(t));
            }
            return;
        }

        ModelParam param = model.param();
        boolean paramExists = false;
        for (String v : param.varnames()) {
            if (v.equals(paramName)) {
                paramExists = true;
                break;
            }
        }
        if (!paramExists) {
            outputError("NODE_NOT_FOUND", "Parameter does not exist in model: " + paramName, "Available: " + Arrays.toString(param.varnames()));
            return;
        }

        String oldExpr = param.get(paramName);
        Double oldValue = null;
        try { oldValue = param.evaluate(paramName); } catch (Exception ignored) {}
        String oldUnit = "";
        try { oldUnit = param.evaluateUnit(paramName); } catch (Exception ignored) {}

        // Set parameter
        try {
            if (descr != null && !descr.isEmpty()) {
                param.set(paramName, paramExpr, descr);
            } else {
                param.set(paramName, paramExpr);
            }
        } catch (Throwable t) {
            outputError("INVALID_PARAMETER", "Failed to set parameter " + paramName + " to '" + paramExpr + "': " + t.getMessage(), getStackTrace(t));
            return;
        }

        // Verify with readback
        String readbackExpr = param.get(paramName);
        Double readbackValue = null;
        String readbackUnit = "";
        try {
            readbackValue = param.evaluate(paramName);
        } catch (Throwable t) {
            outputError("INVALID_PARAMETER", "Parameter readback evaluation failed for " + paramName + ": " + t.getMessage(), getStackTrace(t));
            return;
        }
        try {
            readbackUnit = param.evaluateUnit(paramName);
        } catch (Throwable t) {
            outputError("INVALID_UNIT", "Parameter unit evaluation failed for " + paramName + ": " + t.getMessage(), getStackTrace(t));
            return;
        }

        if (readbackExpr == null || !readbackExpr.equals(paramExpr)) {
            outputError("MODEL_VALIDATION_FAILED", "Readback expression mismatch: expected '" + paramExpr + "', got '" + readbackExpr + "'", "");
            return;
        }

        try {
            model.save(outputPath);
        } catch (Throwable t) {
            outputError("MODEL_VALIDATION_FAILED", "Failed to save modified model to " + outputPath + ": " + t.getMessage(), getStackTrace(t));
            return;
        }

        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": \"SUCCESS\",\n");
        sb.append("  \"operation\": \"update_parameter\",\n");
        sb.append("  \"param_name\": ").append(quote(paramName)).append(",\n");
        sb.append("  \"old_expr\": ").append(quote(oldExpr)).append(",\n");
        sb.append("  \"old_value\": ").append(oldValue == null ? "null" : oldValue.toString()).append(",\n");
        sb.append("  \"old_unit\": ").append(quote(oldUnit)).append(",\n");
        sb.append("  \"new_expr\": ").append(quote(paramExpr)).append(",\n");
        sb.append("  \"readback_value\": ").append(readbackValue == null ? "null" : readbackValue.toString()).append(",\n");
        sb.append("  \"readback_unit\": ").append(quote(readbackUnit)).append(",\n");
        sb.append("  \"source_model\": ").append(quote(modelPath)).append(",\n");
        sb.append("  \"output_model\": ").append(quote(outputPath)).append("\n");
        sb.append("}\n");

        emitResult(sb.toString());
    }

    private static void handleRunStudy(String modelPath, String outputPath, String studyTag, String tlistOverride) {
        if (modelPath.equalsIgnoreCase(outputPath)) {
            outputError("MODEL_VALIDATION_FAILED", "Refusing to overwrite baseline model: source and output path are identical", "");
            return;
        }

        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            String msg = t.getMessage() != null ? t.getMessage() : "";
            if (msg.toLowerCase().contains("not found") || msg.toLowerCase().contains("no such file") || msg.toLowerCase().contains("cannot find")) {
                outputError("MODEL_NOT_FOUND", "Source model file not found: " + modelPath, getStackTrace(t));
            } else {
                outputError("COMSOL_CONNECTION_FAILED", "Failed to load model: " + msg, getStackTrace(t));
            }
            return;
        }

        boolean studyFound = false;
        for (String stag : model.study().tags()) {
            if (stag.equals(studyTag)) {
                studyFound = true;
                break;
            }
        }
        if (!studyFound) {
            outputError("NODE_NOT_FOUND", "Study '" + studyTag + "' not found in model.", "Available: " + Arrays.toString(model.study().tags()));
            return;
        }

        Study study = model.study(studyTag);
        if (tlistOverride != null && !tlistOverride.trim().isEmpty()) {
            try {
                study.feature("time").set("tlist", tlistOverride.trim());
            } catch (Throwable t) {
                outputError("INVALID_PARAMETER", "Failed to apply tlist override '" + tlistOverride + "': " + t.getMessage(), getStackTrace(t));
                return;
            }
        }

        long tStart = System.currentTimeMillis();
        try {
            study.run();
        } catch (Throwable t) {
            outputError("SOLVER_FAILED", "COMSOL solver execution failed: " + t.getMessage(), getStackTrace(t));
            return;
        }
        long elapsedMs = System.currentTimeMillis() - tStart;

        String[] solTags = model.sol().tags();
        if (solTags.length == 0) {
            outputError("SOLVER_FAILED", "Solver run completed but generated no solution nodes.", "");
            return;
        }

        SolverSequence solSeq = model.sol(solTags[0]);
        double[] pvals = null;
        try {
            pvals = solSeq.getPVals();
        } catch (Throwable t) {}

        if (pvals == null || pvals.length == 0) {
            outputError("SOLVER_FAILED", "Solver completed but no time step solution values were recorded.", "");
            return;
        }

        int numDofs = 0;
        boolean hasNanOrInf = false;
        boolean hasPhysicalField = false;
        try {
            double[] uvec = solSeq.getU(1);
            if (uvec != null && uvec.length > 0) {
                numDofs = uvec.length;
                for (double v : uvec) {
                    if (Double.isNaN(v) || Double.isInfinite(v)) {
                        hasNanOrInf = true;
                        break;
                    }
                    if (Math.abs(v) > 1e-12) {
                        hasPhysicalField = true;
                    }
                }
            }
        } catch (Throwable t) {
            outputError("SOLVER_FAILED", "Failed to retrieve solution field vector: " + t.getMessage(), getStackTrace(t));
            return;
        }

        if (hasNanOrInf) {
            outputError("SOLVER_FAILED", "Solver produced invalid numerical values (NaN or Inf detected in solution vector).", "");
            return;
        }
        if (!hasPhysicalField) {
            outputError("SOLVER_FAILED", "Solver produced trivial all-zero field data.", "");
            return;
        }

        try {
            model.save(outputPath);
        } catch (Throwable t) {
            outputError("MODEL_VALIDATION_FAILED", "Failed to save solved model: " + t.getMessage(), getStackTrace(t));
            return;
        }

        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": \"SUCCESS\",\n");
        sb.append("  \"operation\": \"run_study\",\n");
        sb.append("  \"study_tag\": ").append(quote(studyTag)).append(",\n");
        sb.append("  \"elapsed_ms\": ").append(elapsedMs).append(",\n");
        sb.append("  \"sol_tags\": ").append(stringArrayToJson(solTags)).append(",\n");
        sb.append("  \"solution_stats\": {\n");
        sb.append("    \"num_dofs\": ").append(numDofs).append(",\n");
        sb.append("    \"num_timesteps\": ").append(pvals.length).append(",\n");
        sb.append("    \"time_min\": ").append(pvals[0]).append(",\n");
        sb.append("    \"time_max\": ").append(pvals[pvals.length - 1]).append(",\n");
        sb.append("    \"has_nan_or_inf\": false,\n");
        sb.append("    \"has_physical_field\": true\n");
        sb.append("  },\n");
        sb.append("  \"source_model\": ").append(quote(modelPath)).append(",\n");
        sb.append("  \"output_model\": ").append(quote(outputPath)).append("\n");
        sb.append("}\n");

        emitResult(sb.toString());
    }

    private static void outputError(String errorCode, String message, String details) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n");
        sb.append("  \"status\": \"ERROR\",\n");
        sb.append("  \"error_code\": ").append(quote(errorCode)).append(",\n");
        sb.append("  \"message\": ").append(quote(message)).append(",\n");
        sb.append("  \"details\": ").append(quote(details != null ? details : "")).append("\n");
        sb.append("}\n");
        emitResult(sb.toString());
    }

    private static void emitResult(String json) {
        System.out.println(JSON_START);
        System.out.println(json);
        System.out.println(JSON_END);
    }

    private static String quote(String s) {
        if (s == null) return "null";
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '\"': sb.append("\\\""); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < ' ') {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append("\"");
        return sb.toString();
    }

    private static String stringArrayToJson(String[] arr) {
        if (arr == null) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            sb.append(quote(arr[i]));
            if (i < arr.length - 1) sb.append(", ");
        }
        sb.append("]");
        return sb.toString();
    }

    private static String intArrayToJson(int[] arr) {
        if (arr == null) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            sb.append(arr[i]);
            if (i < arr.length - 1) sb.append(", ");
        }
        sb.append("]");
        return sb.toString();
    }

    private static String invokeStringMethod(Object obj, String methodName) {
        if (obj == null) return "";
        try {
            Object res = obj.getClass().getMethod(methodName).invoke(obj);
            return res != null ? res.toString() : "";
        } catch (Exception e) {
            return "";
        }
    }

    private static String getStackTrace(Throwable t) {
        StringWriter sw = new StringWriter();
        t.printStackTrace(new PrintWriter(sw));
        return sw.toString();
    }
}
