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
            } else if ("inspect-native-mesh".equals(command)) {
                if (args.length < 2) {
                    outputError("INVALID_PARAMETER", "Missing model path for inspect-native-mesh", "");
                    return;
                }
                handleInspectNativeMesh(args[1]);
            } else if ("refine-native-mesh".equals(command)) {
                if (args.length != 6) {
                    outputError("INVALID_PARAMETER", "Usage: refine-native-mesh <source> <output> <component> <mesh> <base64Plan>", "");
                    return;
                }
                handleRefineNativeMesh(args[1], args[2], args[3], args[4], args[5]);
            } else if ("update-param".equals(command)) {
                if (args.length < 5) {
                    outputError("INVALID_PARAMETER", "Usage: update-param <modelPath> <outputPath> <paramName> <paramExpr> [paramDescr]", "");
                    return;
                }
                String descr = (args.length >= 6) ? args[5] : "";
                handleUpdateParam(args[1], args[2], args[3], args[4], descr);
            } else if ("run-study".equals(command)) {
                if (args.length < 4) {
                    outputError("INVALID_PARAMETER", "Usage: run-study <modelPath> <outputPath> <studyTag> [tlistOverride] [rtolOverride] [maxstepOverride]", "");
                    return;
                }
                String tlist = (args.length >= 5) ? args[4] : "";
                String rtol = (args.length >= 6) ? args[5] : "";
                String maxstep = (args.length >= 7) ? args[6] : "";
                handleRunStudy(args[1], args[2], args[3], tlist, rtol, maxstep);
            } else if ("build-re100-case".equals(command)) {
                if (args.length < 3) {
                    outputError("INVALID_PARAMETER", "Usage: build-re100-case <modelPath> <outputPath>", "");
                    return;
                }
                handleBuildRe100Case(args[1], args[2]);
            } else if ("build-c04-case".equals(command)) {
                if (args.length < 3) {
                    outputError("INVALID_PARAMETER", "Usage: build-c04-case <modelPath> <outputPath>", "");
                    return;
                }
                handleBuildC04Case(args[1], args[2]);
            } else if ("build-c04-mesh2".equals(command)) {
                if (args.length < 3) {
                    outputError("INVALID_PARAMETER", "Usage: build-c04-mesh2 <modelPath> <outputPath>", "");
                    return;
                }
                handleBuildC04Mesh2(args[1], args[2]);
            } else if ("export-c04-slice".equals(command)) {
                if (args.length < 3) {
                    outputError("INVALID_PARAMETER", "Usage: export-c04-slice <modelPath> <outputTxtPath> [datasetTag] [solnum]", "");
                    return;
                }
                String dsetTag = (args.length >= 4) ? args[3] : "auto";
                String solnum = (args.length >= 5) ? args[4] : "auto";
                handleExportC04Slice(args[1], args[2], dsetTag, solnum);
            } else if ("export-c04-volume".equals(command)) {
                if (args.length < 3) {
                    outputError("INVALID_PARAMETER", "Usage: export-c04-volume <modelPath> <outputTxtPath> [datasetTag] [solnum]", "");
                    return;
                }
                String dsetTag = (args.length >= 4) ? args[3] : "auto";
                String solnum = (args.length >= 5) ? args[4] : "auto";
                handleExportC04Volume(args[1], args[2], dsetTag, solnum);
            } else if ("export-solution-data".equals(command)) {
                if (args.length < 3) {
                    outputError("INVALID_PARAMETER", "Usage: export-solution-data <modelPath> <outputTxtPath>", "");
                    return;
                }
                handleExportSolutionData(args[1], args[2]);
            } else if ("inspect-native-state".equals(command)) {
                if (args.length != 4) {
                    outputError("INVALID_PARAMETER", "Usage: inspect-native-state <model> <solutionTag> <solnum>", "");
                    return;
                }
                Model nativeModel = ModelUtil.loadCopy("Model", args[1]);
                SolverSequence nativeSol = nativeModel.sol(args[2]);
                int nativeIndex = Integer.parseInt(args[3]);
                emitResult("{\"status\":\"SUCCESS\",\"operation\":\"inspect_native_state\",\"model_path\":" + quote(args[1])
                    + ",\"solution_type\":" + quote(nativeSol.getType()) + ",\"parameter_names\":" + stringArrayToJson(nativeSol.getPNames())
                    + ",\"U\":" + branchVectorJson(nativeSol, nativeIndex) + ",\"UDot\":" + branchDerivativeJson(nativeSol, nativeIndex) + "}");
            } else if ("prepare-branch-state".equals(command)) {
                if (args.length != 4) {
                    outputError("INVALID_PARAMETER", "Usage: prepare-branch-state <source> <checkpoint> <branchTimeSeconds>", "");
                    return;
                }
                handlePrepareBranchState(args[1], args[2], args[3]);
            } else if ("run-pulse-branch".equals(command)) {
                if (args.length != 5) {
                    outputError("INVALID_PARAMETER", "Usage: run-pulse-branch <checkpoint> <output> <accelerationAmplitude_m_s2> <restartProtocolId>", "");
                    return;
                }
                handleRunPulseBranch(args[1], args[2], args[3], args[4]);
            } else if ("run-pulse-series-branch".equals(command)) {
                if (args.length != 6) {
                    outputError("INVALID_PARAMETER", "Usage: run-pulse-series-branch <checkpoint> <output> <accelerationAmplitude_m_s2> <restartProtocolId> <seriesId>", "");
                    return;
                }
                handleRunPulseBranch(args[1], args[2], args[3], args[4], args[5]);
            } else if ("inspect-local-pulse".equals(command) || "run-local-pulse".equals(command)) {
                if (args.length != 4) {
                    outputError("INVALID_PARAMETER", "Usage: " + command + " <checkpoint> <output> <base64:properties>", "");
                    return;
                }
                ConfigurablePulseBranch.handle(args[1], args[2], args[3], "run-local-pulse".equals(command));
            } else if ("export-full-field-sample".equals(command)
                    || "export-full-field-sequence-batch".equals(command)) {
                if (args.length < 10) {
                    outputError("INVALID_PARAMETER", "Usage: " + command + " <modelPath> <outputDir> <timesCsv> <nx> <ny> <xmin> <xmax> <ymin> <ymax> [datasetTag]", "");
                    return;
                }
                handleExportFullFieldSample(args[1], args[2], args[3], args[4], args[5], args[6], args[7], args[8], args[9],
                    args.length >= 11 ? args[10] : "dset1", "export-full-field-sequence-batch".equals(command));
            } else if ("export-s1-diagnostics".equals(command)) {
                if (args.length < 3) {
                    outputError("INVALID_PARAMETER", "Usage: export-s1-diagnostics <modelPath> <outputDir> [snapshotTimes]", "");
                    return;
                }
                String snapTimes = (args.length >= 4) ? args[3] : "";
                handleExportS1Diagnostics(args[1], args[2], snapTimes);
            } else if ("inspect-probe-and-solver".equals(command)) {
                if (args.length < 2) {
                    outputError("INVALID_PARAMETER", "Usage: inspect-probe-and-solver <modelPath> [probeX] [probeY] [probeOutputDir]", "");
                    return;
                }
                String px = (args.length >= 3) ? args[2] : "0.15";
                String py = (args.length >= 4) ? args[3] : "0.12";
                String probeOutputDir = (args.length >= 5) ? args[4] : "";
                handleInspectProbeAndSolver(args[1], px, py, probeOutputDir);
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
                sb.append("          \"features\": ").append(stringArrayToJson(geom.feature().tags())).append(",\n");
                sb.append("          \"boundary_geometry\": ").append(boundaryGeometryJson(geom, gtag, "          ")).append("\n");
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
                sb.append("          \"type\": ").append(quote(sfeat.getType())).append(",\n");
                sb.append("          \"properties\": {\n");
                try {
                    String[] props = sfeat.properties();
                    for (int p = 0; p < props.length; p++) {
                        String prop = props[p];
                        String val = "";
                        try { val = sfeat.getString(prop); } catch (Throwable t1) {
                            try { val = Arrays.toString(sfeat.getStringArray(prop)); } catch (Throwable t2) {}
                        }
                        sb.append("            ").append(quote(prop)).append(": ").append(quote(val != null ? val : ""))
                          .append(p < props.length - 1 ? "," : "").append("\n");
                    }
                } catch (Throwable ignored) {}
                sb.append("          }\n");
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
            sb.append("      \"feature_tags\": ").append(stringArrayToJson(sol.feature().tags())).append(",\n");
            sb.append("      \"features\": [\n");
            String[] sftags = sol.feature().tags();
            for (int k = 0; k < sftags.length; k++) {
                String sftag = sftags[k];
                SolverFeature sfeat = sol.feature(sftag);
                sb.append("        {\n");
                sb.append("          \"tag\": ").append(quote(sftag)).append(",\n");
                sb.append("          \"type\": ").append(quote(sfeat.getType())).append(",\n");
                sb.append("          \"label\": ").append(quote(sfeat.label())).append(",\n");
                sb.append("          \"properties\": {\n");
                try {
                    String[] props = sfeat.properties();
                    for (int p = 0; p < props.length; p++) {
                        String prop = props[p];
                        String val = "";
                        try { val = sfeat.getString(prop); } catch (Throwable t1) {
                            try { val = Arrays.toString(sfeat.getStringArray(prop)); } catch (Throwable t2) {}
                        }
                        sb.append("            ").append(quote(prop)).append(": ").append(quote(val != null ? val : ""))
                          .append(p < props.length - 1 ? "," : "").append("\n");
                    }
                } catch (Throwable ignored) {}
                sb.append("          }\n");
                sb.append("        }").append(k < sftags.length - 1 ? "," : "").append("\n");
            }
            sb.append("      ]\n");
            sb.append("    }").append(i < solTags.length - 1 ? "," : "").append("\n");
        }
        sb.append("  ],\n");

        // Datasets
        sb.append("  \"datasets\": [\n");
        String[] dtags = model.result().dataset().tags();
        for (int i = 0; i < dtags.length; i++) {
            String dtag = dtags[i];
            DatasetFeature dset = model.result().dataset(dtag);
            String dtype = dset.getType();
            String label = "";
            try { label = dset.label(); } catch (Throwable ignored) {}
            String sol = "";
            try { sol = dset.getString("solution"); } catch (Throwable ignored) {}
            String dataSrc = "";
            try { dataSrc = dset.getString("data"); } catch (Throwable ignored) {}
            sb.append("    {\n");
            sb.append("      \"tag\": ").append(quote(dtag)).append(",\n");
            sb.append("      \"type\": ").append(quote(dtype)).append(",\n");
            sb.append("      \"label\": ").append(quote(label)).append(",\n");
            sb.append("      \"solution\": ").append(quote(sol)).append(",\n");
            sb.append("      \"data\": ").append(quote(dataSrc)).append("\n");
            sb.append("    }").append(i < dtags.length - 1 ? "," : "").append("\n");
        }
        sb.append("  ]\n");
        sb.append("}\n");

        emitResult(sb.toString());
    }

    private static void handleInspectNativeMesh(String modelPath) throws Exception {
        Model model = ModelUtil.loadCopy("MeshInspection", modelPath);
        emitResult(nativeMeshJson(model, modelPath));
    }

    private static String nativeMeshJson(Model model, String modelPath) {
        StringBuilder sb = new StringBuilder();
        sb.append("{\n  \"status\": \"SUCCESS\",\n  \"operation\": \"inspect_native_mesh\",\n");
        sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
        sb.append("  \"model_tag\": ").append(quote(model.tag())).append(",\n");
        sb.append("  \"comsol_version\": ").append(quote(ModelUtil.getComsolVersion())).append(",\n");
        sb.append("  \"components\": [");
        String[] comps = model.component().tags();
        for (int ci = 0; ci < comps.length; ci++) {
            if (ci > 0) sb.append(",");
            String ctag = comps[ci];
            sb.append("{\"tag\":").append(quote(ctag)).append(",\"meshes\":[");
            String[] meshes = model.component(ctag).mesh().tags();
            for (int mi = 0; mi < meshes.length; mi++) {
                if (mi > 0) sb.append(",");
                String mtag = meshes[mi];
                MeshSequence mesh = model.component(ctag).mesh(mtag);
                sb.append("{\"tag\":").append(quote(mtag))
                  .append(",\"geometry_tag\":").append(quote(mesh.geom()))
                  .append(",\"length_unit\":").append(quote(model.component(ctag).geom(mesh.geom()).lengthUnit()))
                  .append(",\"sdim\":").append(mesh.getSDim())
                  .append(",\"automatic\":").append(mesh.isAutomatic())
                  .append(",\"is_complete\":").append(mesh.isComplete())
                  .append(",\"num_elem\":").append(mesh.getNumElem())
                  .append(",\"num_vertex\":").append(mesh.getNumVertex())
                  .append(",\"quality\":{\"measure\":").append(quote(mesh.stat().getQualityMeasure()))
                  .append(",\"minimum\":").append(mesh.getMinQuality())
                  .append(",\"mean\":").append(mesh.getMeanQuality())
                  .append("},\"area\":{\"minimum\":").append(mesh.getMinVolume())
                  .append(",\"maximum\":").append(mesh.getMaxVolume())
                  .append(",\"total\":").append(mesh.getVolume()).append("}")
                  .append(",\"features\":[");
                String[] fs = mesh.feature().tags();
                for (int fi = 0; fi < fs.length; fi++) {
                    if (fi > 0) sb.append(",");
                    String ftag = fs[fi];
                    appendNativeFeature(sb, mesh.feature(ftag), ftag);
                }
                sb.append("],\"vertices\":").append(nativeValueJson(mesh.getVertex()));
                sb.append(",\"elements\":{");
                String[] types = mesh.getTypes();
                for (int ti = 0; ti < types.length; ti++) {
                    if (ti > 0) sb.append(",");
                    String type = types[ti];
                    sb.append(quote(type)).append(":{\"count\":").append(mesh.getNumElem(type))
                      .append(",\"connectivity\":").append(nativeValueJson(mesh.getElem(type)))
                      .append(",\"entities\":").append(nativeValueJson(mesh.getElemEntity(type))).append("}");
                }
                sb.append("}}");
            }
            sb.append("]}");
        }
        sb.append("],\n  \"non_mesh\":");
        appendNativeNonMesh(sb, model);
        sb.append("\n}");
        return sb.toString();
    }

    /** Apply only explicitly approved size controls, rebuild the native mesh, never solve CFD. */
    private static void handleRefineNativeMesh(String sourcePath, String outputPath, String componentTag,
                                               String meshTag, String encodedPlan) throws Exception {
        if (sourcePath.equalsIgnoreCase(outputPath)) throw new IllegalArgumentException("Source and output must differ");
        Model model = ModelUtil.loadCopy("MeshRefinement", sourcePath);
        MeshSequence mesh = model.component(componentTag).mesh(meshTag);
        if (!"m".equals(model.component(componentTag).geom(mesh.geom()).lengthUnit())) {
            throw new IllegalArgumentException("M1 size values require the verified metre geometry unit");
        }
        String before = nativeMeshJson(model, sourcePath);
        int oldCount = mesh.getNumElem();
        String plan = new String(java.util.Base64.getDecoder().decode(encodedPlan), java.nio.charset.StandardCharsets.UTF_8);
        String[] rows = plan.split("\n");
        MeshFeature[] targets = new MeshFeature[rows.length];
        String[][] fields = new String[rows.length][];
        // Check all preconditions before any setter can affect another default value.
        for (int i = 0; i < rows.length; i++) {
            fields[i] = rows[i].split("\t", -1);
            if (fields[i].length != 5) throw new IllegalArgumentException("Invalid mesh-plan row");
            String[] path = fields[i][0].split("/");
            MeshFeature feature = mesh.feature(path[0]);
            for (int j = 1; j < path.length; j++) feature = feature.feature(path[j]);
            if (!"Size".equals(feature.getType()) && !"MeshSizeDefault".equals(feature.getType())) {
                throw new IllegalArgumentException("M1 changes are limited to existing Size features");
            }
            String property = fields[i][1];
            if (!property.equals("custom") && !property.equals("hmax") && !property.equals("hmin")) {
                throw new IllegalArgumentException("Unapproved mesh property: " + property);
            }
            Object current = nativeReadProperty(feature, property);
            if (!nativePlanMatches(current, fields[i][2], fields[i][3])) {
                throw new IllegalArgumentException("Mesh-plan before value mismatch: " + fields[i][0] + "/" + property);
            }
            targets[i] = feature;
        }
        for (int i = 0; i < rows.length; i++) {
            if (fields[i][2].equals("number")) targets[i].set(fields[i][1], Double.parseDouble(fields[i][4]));
            else targets[i].set(fields[i][1], fields[i][4]);
        }
        String[] solTags = model.sol().tags();
        for (String tag : solTags) model.sol(tag).clearSolutionData();
        mesh.run();
        if (!mesh.isComplete() || mesh.getNumElem() <= oldCount || !(mesh.getMinQuality() > 0)) {
            throw new IllegalStateException("Native refined mesh is incomplete, unchanged, or degenerate");
        }
        for (int i = 0; i < rows.length; i++) {
            if (!nativePlanMatches(nativeReadProperty(targets[i], fields[i][1]), fields[i][2], fields[i][4])) {
                throw new IllegalStateException("Mesh-plan after value mismatch: " + fields[i][0] + "/" + fields[i][1]);
            }
        }
        boolean emptySolutions = true;
        for (String tag : solTags) {
            if (!model.sol(tag).isEmpty()) emptySolutions = false;
        }
        if (!emptySolutions) throw new IllegalStateException("Old solution data remains after mesh refinement");
        String after = nativeMeshJson(model, outputPath);
        model.save(outputPath);
        emitResult("{\"status\":\"SUCCESS\",\"operation\":\"refine_native_mesh\",\"source_model\":"
            + quote(sourcePath) + ",\"output_model\":" + quote(outputPath)
            + ",\"cleared_solution_tags\":" + stringArrayToJson(solTags)
            + ",\"stored_solution_data_empty\":" + emptySolutions
            + ",\"before\":" + before + ",\"after\":" + after + "}");
    }

    private static boolean nativePlanMatches(Object value, String kind, String expected) {
        if (kind.equals("number") && value instanceof Number) {
            double actual = ((Number) value).doubleValue(), wanted = Double.parseDouble(expected);
            return Double.isFinite(actual) && Double.isFinite(wanted)
                && Math.abs(actual - wanted) <= Math.max(Math.abs(wanted), 1e-30) * 1e-12;
        }
        return kind.equals("string") && value instanceof String && value.equals(expected);
    }

    /** Read values through the installed public API; unsupported properties remain explicit. */
    private static Object nativeReadProperty(Object feature, String name) throws Exception {
        String type = String.valueOf(feature.getClass().getMethod("getValueType", String.class).invoke(feature, name));
        if (type.equals("Selection")) {
            Object selection = feature.getClass().getMethod("selection", String.class).invoke(feature, name);
            return selection.getClass().getMethod("entities").invoke(selection);
        }
        String preferred = "get" + type;
        String[] getters = {preferred, "getString", "getStringArray", "getStringMatrix", "getDouble",
            "getDoubleArray", "getDoubleMatrix", "getInt", "getIntArray", "getIntMatrix", "getBoolean",
            "getBooleanArray", "getBooleanMatrix"};
        Exception last = null;
        for (String getter : getters) {
            try { return feature.getClass().getMethod(getter, String.class).invoke(feature, name); }
            catch (Exception e) { last = e; }
        }
        throw new IllegalStateException("No readable getter for " + name + " (" + type + ")", last);
    }

    private static String nativeValueJson(Object value) {
        if (value == null) return "null";
        if (value instanceof String) return quote((String) value);
        if (value instanceof Boolean) return value.toString();
        if (value instanceof Number) {
            double v = ((Number) value).doubleValue();
            if (!Double.isFinite(v)) throw new IllegalStateException("Non-finite native property value");
            return value.toString();
        }
        if (value.getClass().isArray()) {
            StringBuilder b = new StringBuilder("[");
            int n = java.lang.reflect.Array.getLength(value);
            for (int i = 0; i < n; i++) {
                if (i > 0) b.append(",");
                b.append(nativeValueJson(java.lang.reflect.Array.get(value, i)));
            }
            return b.append("]").toString();
        }
        throw new IllegalStateException("Unsupported native value class: " + value.getClass().getName());
    }

    static void appendNativeFeature(StringBuilder sb, Object feature, String path) {
        sb.append("{\"tag\":").append(quote(invokeStringMethod(feature, "tag")))
          .append(",\"path\":").append(quote(path))
          .append(",\"type\":").append(quote(invokeStringMethod(feature, "getType")));
        try { sb.append(",\"active\":").append(feature.getClass().getMethod("isActive").invoke(feature)); }
        catch (Exception e) { sb.append(",\"active\":null"); }
        StringBuilder errors = new StringBuilder("{");
        boolean firstError = true;
        sb.append(",\"properties\":{");
        try {
            String[] props = (String[]) feature.getClass().getMethod("properties").invoke(feature);
            Arrays.sort(props);
            boolean first = true;
            for (String prop : props) {
                try {
                    String value = nativeValueJson(nativeReadProperty(feature, prop));
                    if (!first) sb.append(",");
                    sb.append(quote(prop)).append(":").append(value);
                    first = false;
                } catch (Exception e) {
                    if (!firstError) errors.append(",");
                    errors.append(quote(prop)).append(":").append(quote(e.getMessage()));
                    firstError = false;
                }
            }
        } catch (Exception e) {
            errors.append("\"_properties\":").append(quote(e.getMessage()));
        }
        sb.append("},\"unreadable_properties\":").append(errors).append("}");
        sb.append(",\"selection_entities\":");
        try { sb.append(intArrayToJson(featureEntities(feature))); }
        catch (Exception e) { sb.append("null"); }
        for (String group : new String[]{"feature", "prop", "propertyGroup"}) {
            try {
                java.lang.reflect.Method listMethod = feature.getClass().getMethod(group);
                Object list = listMethod.invoke(feature);
                String[] tags = (String[]) list.getClass().getMethod("tags").invoke(list);
                sb.append(",").append(quote(group.equals("feature") ? "features" : group)).append(":[");
                for (int i = 0; i < tags.length; i++) {
                    if (i > 0) sb.append(",");
                    Object child = feature.getClass().getMethod(group, String.class).invoke(feature, tags[i]);
                    appendNativeFeature(sb, child, path + "/" + tags[i]);
                }
                sb.append("]");
            } catch (NoSuchMethodException e) {
                // This feature type has no children of this category.
            } catch (Exception e) {
                throw new IllegalStateException("Cannot inspect " + path + "/" + group, e);
            }
        }
        sb.append("}");
    }

    private static void appendNativeNonMesh(StringBuilder sb, Model model) {
        sb.append("{\"components\":[");
        String[] tags = model.component().tags();
        for (int i = 0; i < tags.length; i++) {
            if (i > 0) sb.append(",");
            ModelNode comp = model.component(tags[i]);
            sb.append("{\"tag\":").append(quote(tags[i])).append(",\"geometry\":[");
            String[] geoms = comp.geom().tags();
            for (int j = 0; j < geoms.length; j++) {
                if (j > 0) sb.append(",");
                GeomSequence geom = comp.geom(geoms[j]);
                sb.append("{\"tag\":").append(quote(geoms[j])).append(",\"features\":[");
                String[] fs = geom.feature().tags();
                for (int k = 0; k < fs.length; k++) {
                    if (k > 0) sb.append(",");
                    appendNativeFeature(sb, geom.feature(fs[k]), fs[k]);
                }
                sb.append("]}");
            }
            sb.append("],\"physics\":[");
            String[] physics = comp.physics().tags();
            for (int j = 0; j < physics.length; j++) {
                if (j > 0) sb.append(",");
                appendNativeFeature(sb, comp.physics(physics[j]), physics[j]);
            }
            sb.append("],\"materials\":[");
            String[] mats = comp.material().tags();
            for (int j = 0; j < mats.length; j++) {
                if (j > 0) sb.append(",");
                appendNativeFeature(sb, comp.material(mats[j]), mats[j]);
            }
            sb.append("]}");
        }
        sb.append("]}");
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

    private static void handleRunStudy(String modelPath, String outputPath, String studyTag, String tlistOverride, String rtolOverride, String maxstepOverride) {
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

        if (rtolOverride != null && !rtolOverride.trim().isEmpty()) {
            try {
                study.feature("time").set("rtol", rtolOverride.trim());
            } catch (Throwable t) {
                outputError("INVALID_PARAMETER", "Failed to apply rtol override '" + rtolOverride + "': " + t.getMessage(), getStackTrace(t));
                return;
            }
        }

        // Ensure solver sequence exists so we can inspect and configure solver features
        if (model.sol().tags().length == 0) {
            try {
                study.createAutoSequences("sol1");
            } catch (Throwable ignored) {}
        }

        // Apply low-level solver feature parameters (e.g. sol1/t1)
        for (String sTag : model.sol().tags()) {
            SolverSequence sSeq = model.sol(sTag);
            for (String fTag : sSeq.feature().tags()) {
                SolverFeature feat = sSeq.feature(fTag);
                if ("Time".equalsIgnoreCase(feat.getType())) {
                    if (rtolOverride != null && !rtolOverride.trim().isEmpty()) {
                        try { feat.set("rtol", rtolOverride.trim()); } catch (Throwable ignored) {}
                    }
                    if (maxstepOverride != null && !maxstepOverride.trim().isEmpty()) {
                        try {
                            feat.set("maxstepconstraintbdf", "const");
                            feat.set("maxstepbdf", maxstepOverride.trim());
                        } catch (Throwable ignored) {}
                    }
                }
            }
        }

        long tStart = System.currentTimeMillis();
        try {
            study.runNoGen();
        } catch (Throwable t) {
            try {
                study.run();
            } catch (Throwable t2) {
                outputError("SOLVER_FAILED", "COMSOL solver execution failed: " + t2.getMessage(), getStackTrace(t2));
                return;
            }
        }
        long elapsedMs = System.currentTimeMillis() - tStart;

        String[] solTags = model.sol().tags();
        if (solTags.length == 0) {
            outputError("SOLVER_FAILED", "Solver run completed but generated no solution nodes.", "");
            return;
        }

        // Identify the solver sequence that corresponds to the executed study
        SolverSequence solSeq = null;
        for (String sTag : solTags) {
            SolverSequence s = model.sol(sTag);
            try {
                for (String fTag : s.feature().tags()) {
                    SolverFeature sf = s.feature(fTag);
                    if ("StudyStep".equalsIgnoreCase(sf.getType())) {
                        try {
                            if (studyTag.equals(sf.getString("study"))) {
                                solSeq = s;
                                break;
                            }
                        } catch (Throwable ignored) {}
                    }
                }
            } catch (Throwable ignored) {}
            if (solSeq != null) break;
        }

        if (solSeq == null) {
            for (String sTag : solTags) {
                SolverSequence s = model.sol(sTag);
                try {
                    double[] pv = s.getPVals();
                    if (pv != null && pv.length > 0) {
                        solSeq = s;
                        break;
                    }
                } catch (Throwable ignored) {}
            }
        }

        if (solSeq == null) {
            solSeq = model.sol(solTags[0]);
        }

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

        // Live solver parameters readback directly from solved solSeq
        String liveRtol = "";
        String liveTimeMethod = "";
        String liveTstepsBdf = "";
        String liveMaxOrder = "";
        String liveMinOrder = "";
        String liveMaxStepConstraint = "";
        String liveMaxStep = "";
        String liveTlist = "";
        try {
            for (String fTag : solSeq.feature().tags()) {
                SolverFeature feat = solSeq.feature(fTag);
                if ("Time".equalsIgnoreCase(feat.getType())) {
                    try { liveRtol = feat.getString("rtol"); } catch (Throwable ignored) {}
                    try { liveTimeMethod = feat.getString("timemethod"); } catch (Throwable ignored) {}
                    try { liveTstepsBdf = feat.getString("tstepsbdf"); } catch (Throwable ignored) {}
                    try { liveMaxOrder = feat.getString("maxorder"); } catch (Throwable ignored) {}
                    try { liveMinOrder = feat.getString("minorder"); } catch (Throwable ignored) {}
                    try { liveMaxStepConstraint = feat.getString("maxstepconstraintbdf"); } catch (Throwable ignored) {}
                    try { liveMaxStep = feat.getString("maxstepbdf"); } catch (Throwable ignored) {}
                    try { liveTlist = feat.getString("tlist"); } catch (Throwable ignored) {}
                    break;
                }
            }
        } catch (Throwable ignored) {}

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
        sb.append("  \"sol_tag\": ").append(quote(solSeq.tag())).append(",\n");
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
        sb.append("  \"solver_readback\": {\n");
        sb.append("    \"source\": \"LIVE_API_READBACK\",\n");
        sb.append("    \"timemethod\": ").append(quote(liveTimeMethod)).append(",\n");
        sb.append("    \"tstepsbdf\": ").append(quote(liveTstepsBdf)).append(",\n");
        sb.append("    \"maxorder\": ").append(quote(liveMaxOrder)).append(",\n");
        sb.append("    \"minorder\": ").append(quote(liveMinOrder)).append(",\n");
        sb.append("    \"rtol\": ").append(quote(liveRtol)).append(",\n");
        sb.append("    \"maxstepconstraintbdf\": ").append(quote(liveMaxStepConstraint)).append(",\n");
        sb.append("    \"maxstepbdf\": ").append(quote(liveMaxStep)).append(",\n");
        sb.append("    \"tlist\": ").append(quote(liveTlist)).append("\n");
        sb.append("  },\n");
        sb.append("  \"source_model\": ").append(quote(modelPath)).append(",\n");
        sb.append("  \"output_model\": ").append(quote(outputPath)).append("\n");
        sb.append("}\n");

        emitResult(sb.toString());
    }

    private static void handleBuildRe100Case(String modelPath, String outputPath) {
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

        try {
            ModelParam param = model.param();
            // Physical & geometric parameters:
            // 20 degC freshwater: rho = 998.2 kg/m^3, mu = 1.004e-3 Pa*s
            // Cylinder D = 0.01 m, R = 0.005 m
            // Reynolds number Re = 100
            // Inflow U_inf = Re*mu/(rho*D) = 0.010058104588 m/s
            // Domain: L = 40D = 0.40 m, H = 24D = 0.24 m (blockage = 4.167% < 5%)
            // Upstream = 10D (x_c = 0.10 m), Downstream = 30D, Lateral half-width = 12D (y_c = 0.12 m)
            param.set("D", "0.01[m]", "Cylinder diameter");
            param.set("R", "D/2", "Cylinder radius");
            param.set("L", "0.40[m]", "Domain length (40D)");
            param.set("H", "0.24[m]", "Domain height (24D)");
            param.set("x_c", "0.10[m]", "Cylinder center X (10D upstream distance)");
            param.set("y_c", "0.12[m]", "Cylinder center Y (12D lateral half-width)");
            param.set("rho_f", "998.2[kg/m^3]", "Water density at 20 degC");
            param.set("mu_f", "1.004e-3[Pa*s]", "Water dynamic viscosity at 20 degC");
            param.set("Re", "100", "Target Reynolds number");
            param.set("U_inf", "Re*mu_f/(rho_f*D)", "Inflow free-stream velocity");
            param.set("U_mean", "U_inf", "Mean inflow velocity alias");

            ModelNode comp = model.component("comp1");
            GeomSequence geom = comp.geom("geom1");
            geom.feature("c1").set("pos", new String[]{"x_c", "y_c"});
            geom.feature("c1").set("r", "R");
            geom.feature("r1").set("lx", "L");
            geom.feature("r1").set("ly", "H");
            geom.run();

            // Material update - fail closed
            Material mat = comp.material("mat1");
            mat.propertyGroup("def").set("density", "rho_f");
            mat.propertyGroup("def").set("dynamicviscosity", "mu_f");

            // Physics update
            Object spf = comp.physics("spf");
            Object featList = spf.getClass().getMethod("feature").invoke(spf);

            // fp1: Fluid properties
            Object fp1 = spf.getClass().getMethod("feature", String.class).invoke(spf, "fp1");
            fp1.getClass().getMethod("set", String.class, String.class).invoke(fp1, "rho", "rho_f");
            fp1.getClass().getMethod("set", String.class, String.class).invoke(fp1, "mu", "mu_f");

            // inl1: Boundary 1 (Inlet) - uniform normal inflow with smooth start step1
            Object inl1 = spf.getClass().getMethod("feature", String.class).invoke(spf, "inl1");
            inl1.getClass().getMethod("set", String.class, String.class).invoke(inl1, "BoundaryCondition", "Velocity");
            inl1.getClass().getMethod("set", String.class, String.class).invoke(inl1, "ComponentWise", "NormalInflowVelocity");
            inl1.getClass().getMethod("set", String.class, String.class).invoke(inl1, "U0in", "U_inf*step1(t[1/s])");

            // out1: Boundary 4 (Outlet) - pressure outlet p=0 with suppress backflow
            Object out1 = spf.getClass().getMethod("feature", String.class).invoke(spf, "out1");
            out1.getClass().getMethod("set", String.class, String.class).invoke(out1, "p0", "0");
            out1.getClass().getMethod("set", String.class, String.class).invoke(out1, "SuppressBackflow", "1");

            // Boundary identification from the LIVE geometry (no assumed IDs).
            // Classifies every boundary edge sampled via GeomSequence.edgeX against the
            // geometry bounding box and the evaluated cylinder parameters.
            int[][] cls = classifyCylinderChannelBoundaries(geom, param.evaluate("x_c"), param.evaluate("y_c"), param.evaluate("R"));
            int[] inletIds = cls[0], outletIds = cls[1], lateralIds = cls[2], cylinderIds = cls[3];

            // sym1: lateral (y = ymin / y = ymax) boundaries - Symmetry condition (fail closed)
            Object sym = null;
            try {
                java.lang.reflect.Method createM = featList.getClass().getMethod("create", String.class, String.class, int.class);
                sym = createM.invoke(featList, "sym1", "Symmetry", 1);
            } catch (Throwable t) {
                sym = spf.getClass().getMethod("feature", String.class).invoke(spf, "sym1");
            }
            Object symSel = sym.getClass().getMethod("selection").invoke(sym);
            symSel.getClass().getMethod("set", int[].class).invoke(symSel, lateralIds);

            // Read back every key feature selection and require exact agreement with geometry
            Object wall = spf.getClass().getMethod("feature", String.class).invoke(spf, "wallbc1");
            requireSameEntities("inl1 (inlet, x = xmin)", featureEntities(inl1), inletIds);
            requireSameEntities("out1 (outlet, x = xmax)", featureEntities(out1), outletIds);
            requireSameEntities("sym1 (lateral, y = ymin/ymax)", featureEntities(sym), lateralIds);
            requireSameEntities("wallbc1 (cylinder no-slip, |r - r_c| = R)", featureEntities(wall), cylinderIds);

            // Mesh configuration: cylinder boundary layer and near-wall refinement (fail closed)
            MeshSequence mesh = comp.mesh("mesh1");
            MeshFeature bl1 = mesh.feature("bl1");
            bl1.feature("blp1").selection().set(cylinderIds);

            MeshFeature size1 = mesh.feature("size1");
            size1.selection().set(cylinderIds);
            size1.set("hmax", "0.0004[m]");
            size1.set("hmin", "0.00005[m]");
            requireSameEntities("mesh bl1/blp1", featureEntities(bl1.feature("blp1")), cylinderIds);
            requireSameEntities("mesh size1", featureEntities(size1), cylinderIds);

            mesh.run();
            if (!mesh.isComplete()) {
                throw new IllegalStateException("Mesh build failed or is incomplete");
            }

            // Study configuration
            Study study = model.study("std1");
            StudyFeature timeFeat = study.feature("time");
            timeFeat.set("tlist", "range(0, 0.05, 0.5)");
            timeFeat.set("rtol", "0.001");

            model.save(outputPath);

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"build_re100_case\",\n");
            sb.append("  \"source_model\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"output_model\": ").append(quote(outputPath)).append(",\n");
            sb.append("  \"parameters\": {\n");
            sb.append("    \"Re\": ").append(param.evaluate("Re")).append(",\n");
            sb.append("    \"rho\": ").append(param.evaluate("rho_f")).append(",\n");
            sb.append("    \"mu\": ").append(param.evaluate("mu_f")).append(",\n");
            sb.append("    \"D\": ").append(param.evaluate("D")).append(",\n");
            sb.append("    \"U_inf\": ").append(param.evaluate("U_inf")).append(",\n");
            sb.append("    \"L\": ").append(param.evaluate("L")).append(",\n");
            sb.append("    \"H\": ").append(param.evaluate("H")).append(",\n");
            sb.append("    \"x_c\": ").append(param.evaluate("x_c")).append(",\n");
            sb.append("    \"y_c\": ").append(param.evaluate("y_c")).append(",\n");
            sb.append("    \"blockage_ratio\": ").append(param.evaluate("D") / param.evaluate("H")).append("\n");
            sb.append("  },\n");
            sb.append("  \"mesh_stats\": {\n");
            sb.append("    \"num_elem\": ").append(mesh.getNumElem()).append(",\n");
            sb.append("    \"num_vertex\": ").append(mesh.getNumVertex()).append("\n");
            sb.append("  },\n");
            sb.append("  \"boundary_classification\": {\n");
            sb.append("    \"method\": \"geometric classification of live GeomSequence.edgeX samples against bounding box and evaluated x_c, y_c, R\",\n");
            sb.append("    \"inlet\": ").append(intArrayToJson(inletIds)).append(",\n");
            sb.append("    \"outlet\": ").append(intArrayToJson(outletIds)).append(",\n");
            sb.append("    \"lateral\": ").append(intArrayToJson(lateralIds)).append(",\n");
            sb.append("    \"cylinder\": ").append(intArrayToJson(cylinderIds)).append("\n");
            sb.append("  },\n");
            sb.append("  \"feature_selections_readback\": {\n");
            sb.append("    \"inl1\": ").append(intArrayToJson(featureEntities(inl1))).append(",\n");
            sb.append("    \"out1\": ").append(intArrayToJson(featureEntities(out1))).append(",\n");
            sb.append("    \"sym1\": ").append(intArrayToJson(featureEntities(sym))).append(",\n");
            sb.append("    \"wallbc1\": ").append(intArrayToJson(featureEntities(wall))).append(",\n");
            sb.append("    \"bl1/blp1\": ").append(intArrayToJson(featureEntities(bl1.feature("blp1")))).append(",\n");
            sb.append("    \"size1\": ").append(intArrayToJson(featureEntities(size1))).append("\n");
            sb.append("  },\n");
            sb.append("  \"boundary_geometry\": ").append(boundaryGeometryJson(geom, "geom1", "  ")).append("\n");
            sb.append("}\n");
            emitResult(sb.toString());

        } catch (Throwable t) {
            outputError("MODEL_VALIDATION_FAILED", "Failed to build Re=100 benchmark model: " + t.getMessage(), getStackTrace(t));
        }
    }

    private static void handleBuildC04Case(String modelPath, String outputPath) {
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

        try {
            ModelParam param = model.param();
            param.set("D", "0.30[m]", "Cylinder diameter");
            param.set("U0", "0.50[m/s]", "Inflow velocity magnitude");
            param.set("alpha", "90[deg]", "Inflow angle");
            param.set("rho0", "1025.8944[kg/m^3]", "Seawater density at 15 degC (ITTC 2024)");
            param.set("nu0", "1.1889e-6[m^2/s]", "Seawater kinematic viscosity (ITTC 2024)");
            param.set("mu0", "rho0*nu0", "Seawater dynamic viscosity");
            param.set("ReD", "U0*D/nu0", "Cylinder Reynolds number");
            param.set("Ux", "U0*sin(alpha)", "Inflow X velocity");
            param.set("Uy", "0[m/s]", "Inflow Y velocity");
            param.set("Uz", "U0*cos(alpha)", "Inflow Z velocity");
            param.set("L_span", "10*D", "Spanwise domain depth (Lysenko 2025)");

            // Aliases to preserve existing expressions in official model
            param.set("rhof", "rho0", "Fluid density alias");
            param.set("visc", "mu0", "Fluid dynamic viscosity alias");
            param.set("Uin", "U0", "Inlet velocity alias");
            param.set("Re", "ReD", "Reynolds number alias");

            ModelNode comp = model.component("comp1");
            GeomSequence geom = comp.geom("geom1");
            geom.feature("ext1").set("distance", "L_span");
            geom.run();

            // Mesh 1: scale controls to D = 0.30 m for initial stationary calculation
            try {
                MeshSequence mesh1 = comp.mesh("mesh1");
                double scale = param.evaluate("D") / 0.004548;
                MeshFeature size = mesh1.feature("size");
                size.set("hmax", (0.00288 * scale) + "[m]");
                size.set("hmin", (8.63e-4 * scale) + "[m]");
                MeshFeature size1 = mesh1.feature("size1");
                size1.set("hmax", (0.00288 * scale) + "[m]");
                size1.set("hmin", (6.09e-4 * scale) + "[m]");
                MeshFeature size2 = mesh1.feature("size2");
                size2.set("hmax", (0.00193 * scale) + "[m]");
                size2.set("hmin", (5.75e-4 * scale) + "[m]");
                mesh1.run();
            } catch (Throwable ignored) {}

            // Mesh 2: scale and tune controls for transient SST-SAS calculation (1.55M elements)
            try {
                MeshSequence mesh2 = comp.mesh("mesh2");
                double scale = param.evaluate("D") / 0.004548;
                MeshFeature m2size = mesh2.feature("size");
                m2size.set("hmax", (0.006 * scale) + "[m]");
                m2size.set("hmin", (8.63e-4 * scale) + "[m]");
                MeshFeature m2size2 = mesh2.feature("size2");
                m2size2.set("hmax", "D/6");
                m2size2.set("hmin", (1.15e-4 * scale) + "[m]");
                MeshFeature m2bl1 = mesh2.feature("bl1");
                MeshFeature m2blp1 = m2bl1.feature("blp1");
                m2blp1.set("blhtot", (0.001064 * scale) + "[m]");
            } catch (Throwable ignored) {}

            // Update transient study step tlist for C04 time scale (t* = D/U0 = 0.60 s)
            try {
                Study std2 = model.study("std2");
                std2.feature("time").set("tlist", "range(0, 0.06, 3.0)");
            } catch (Throwable ignored) {}

            String[] solTags = model.sol().tags();
            for (String stag : solTags) {
                try {
                    model.sol(stag).clearSolutionData();
                } catch (Throwable ignored) {}
            }

            model.save(outputPath);

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"build_c04_case\",\n");
            sb.append("  \"source_model\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"output_model\": ").append(quote(outputPath)).append(",\n");
            sb.append("  \"parameters\": {\n");
            sb.append("    \"D\": ").append(param.evaluate("D")).append(",\n");
            sb.append("    \"U0\": ").append(param.evaluate("U0")).append(",\n");
            sb.append("    \"alpha\": ").append(param.evaluate("alpha")).append(",\n");
            sb.append("    \"rho0\": ").append(param.evaluate("rho0")).append(",\n");
            sb.append("    \"nu0\": ").append(param.evaluate("nu0")).append(",\n");
            sb.append("    \"mu0\": ").append(param.evaluate("mu0")).append(",\n");
            sb.append("    \"ReD\": ").append(param.evaluate("ReD")).append(",\n");
            sb.append("    \"Ux\": ").append(param.evaluate("Ux")).append(",\n");
            sb.append("    \"Uz\": ").append(param.evaluate("Uz")).append(",\n");
            sb.append("    \"L_span\": ").append(param.evaluate("L_span")).append("\n");
            sb.append("  },\n");
            sb.append("  \"cleared_solution_tags\": ").append(stringArrayToJson(solTags)).append("\n");
            sb.append("}\n");

            emitResult(sb.toString());
        } catch (Throwable t) {
            outputError("BUILD_C04_FAILED", "Failed to build C04 benchmark model: " + t.getMessage(), getStackTrace(t));
        }
    }

    private static void handleBuildC04Mesh2(String modelPath, String outputPath) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            outputError("COMSOL_CONNECTION_FAILED", "Failed to load model: " + t.getMessage(), getStackTrace(t));
            return;
        }

        try {
            ModelNode comp = model.component("comp1");
            MeshSequence mesh2 = comp.mesh("mesh2");
            double scale = model.param().evaluate("D") / 0.004548;

            MeshFeature m2size = mesh2.feature("size");
            m2size.set("hmax", (0.006 * scale) + "[m]");
            m2size.set("hmin", (8.63e-4 * scale) + "[m]");

            MeshFeature m2size2 = mesh2.feature("size2");
            m2size2.set("hmax", "D/6");
            m2size2.set("hmin", (1.15e-4 * scale) + "[m]");

            MeshFeature m2bl1 = mesh2.feature("bl1");
            MeshFeature m2blp1 = m2bl1.feature("blp1");
            m2blp1.set("blhtot", (0.001064 * scale) + "[m]");

            mesh2.run();
            model.save(outputPath);

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"build_c04_mesh2\",\n");
            sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"output_model\": ").append(quote(outputPath)).append(",\n");
            sb.append("  \"num_elements\": ").append(mesh2.getNumElem()).append(",\n");
            sb.append("  \"num_vertices\": ").append(mesh2.getNumVertex()).append("\n");
            sb.append("}\n");
            emitResult(sb.toString());
        } catch (Throwable t) {
            outputError("MESH2_BUILD_FAILED", "Failed to build mesh2: " + t.getMessage(), getStackTrace(t));
        }
    }

    private static void handleExportC04Slice(String modelPath, String outputTxtPath, String dsetTag, String solnum) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            outputError("COMSOL_CONNECTION_FAILED", "Failed to load model for slice export: " + t.getMessage(), getStackTrace(t));
            return;
        }

        try {
            String targetDset = "dset1";
            if (dsetTag != null && !dsetTag.trim().isEmpty() && !dsetTag.equalsIgnoreCase("auto")) {
                targetDset = dsetTag.trim();
            } else {
                String[] dtags = model.result().dataset().tags();
                String fallbackSolDset = null;
                for (int i = dtags.length - 1; i >= 0; i--) {
                    try {
                        DatasetFeature d = model.result().dataset(dtags[i]);
                        if ("Solution".equals(d.getType())) {
                            if (fallbackSolDset == null) {
                                fallbackSolDset = dtags[i];
                            }
                            String sol = "";
                            try { sol = d.getString("solution"); } catch (Throwable ignored) {}
                            if ("sol3".equals(sol) || "sol5".equals(sol)) {
                                targetDset = dtags[i];
                                fallbackSolDset = null;
                                break;
                            }
                        }
                    } catch (Throwable ignored) {}
                }
                if (fallbackSolDset != null) {
                    targetDset = fallbackSolDset;
                }
            }

            safeRemoveDataset(model, "cpl_c04_slice");
            DatasetFeature cutPlane = model.result().dataset().create("cpl_c04_slice", "CutPlane");
            cutPlane.set("data", targetDset);
            cutPlane.set("planetype", "quick");
            cutPlane.set("quickplane", "xz");
            cutPlane.set("quicky", -1.5);

            safeRemoveExport(model, "exp_c04_slice");
            ExportFeature exporter = model.result().export().create("exp_c04_slice", "Data");
            exporter.set("data", cutPlane.tag());
            exporter.set("expr", new String[]{"u", "v", "w", "p"});
            exporter.set("location", "grid");
            exporter.set("gridstruct", "spreadsheet");

            double[] gridx = new double[192];
            for (int i = 0; i < 192; i++) gridx[i] = -0.6 + i * (3.6 / 191.0);
            double[] gridz = new double[96];
            for (int i = 0; i < 96; i++) gridz[i] = -0.9 + i * (1.8 / 95.0);

            exporter.set("gridx2", gridx);
            exporter.set("gridy2", gridz);
            exporter.set("header", "on");
            exporter.set("fullprec", "on");
            exporter.set("includecoords", true);
            exporter.set("includenan", true);

            if (solnum != null && solnum.contains(",")) {
                String[] snums = solnum.split(",");
                for (int s = 0; s < snums.length; s++) {
                    String sn = snums[s].trim();
                    exporter.set("solnum", sn);
                    exporter.set("filename", outputTxtPath + ".step_" + sn);
                    exporter.run();
                }
            } else {
                if (solnum != null && !solnum.trim().isEmpty() && !solnum.equalsIgnoreCase("auto")) {
                    exporter.set("solnum", solnum.trim());
                }
                exporter.set("filename", outputTxtPath);
                exporter.run();
            }

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"export_c04_slice\",\n");
            sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"output_txt\": ").append(quote(outputTxtPath)).append(",\n");
            sb.append("  \"dataset\": ").append(quote(targetDset)).append(",\n");
            sb.append("  \"solnum\": ").append(quote(solnum != null ? solnum : "auto")).append(",\n");
            sb.append("  \"nx\": 192,\n");
            sb.append("  \"ny\": 96\n");
            sb.append("}\n");
            emitResult(sb.toString());
        } catch (Throwable t) {
            outputError("EXPORT_C04_SLICE_FAILED", "Failed to export C04 slice: " + t.getMessage(), getStackTrace(t));
        }
    }

    private static void handleExportC04Volume(String modelPath, String outputTxtPath, String dsetTag, String solnum) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            outputError("COMSOL_CONNECTION_FAILED", "Failed to load model for volume export: " + t.getMessage(), getStackTrace(t));
            return;
        }

        try {
            String targetDset = "dset1";
            if (dsetTag != null && !dsetTag.trim().isEmpty() && !dsetTag.equalsIgnoreCase("auto")) {
                targetDset = dsetTag.trim();
            } else {
                String[] dtags = model.result().dataset().tags();
                String fallbackSolDset = null;
                for (int i = dtags.length - 1; i >= 0; i--) {
                    try {
                        DatasetFeature d = model.result().dataset(dtags[i]);
                        if ("Solution".equals(d.getType())) {
                            if (fallbackSolDset == null) {
                                fallbackSolDset = dtags[i];
                            }
                            String sol = "";
                            try { sol = d.getString("solution"); } catch (Throwable ignored) {}
                            if ("sol3".equals(sol) || "sol5".equals(sol)) {
                                targetDset = dtags[i];
                                fallbackSolDset = null;
                                break;
                            }
                        }
                    } catch (Throwable ignored) {}
                }
                if (fallbackSolDset != null) {
                    targetDset = fallbackSolDset;
                }
            }

            safeRemoveExport(model, "exp_c04_vol");
            ExportFeature exporter = model.result().export().create("exp_c04_vol", "Data");
            exporter.set("data", targetDset);
            exporter.set("expr", new String[]{"u", "v", "w", "p"});
            exporter.set("location", "grid");
            exporter.set("gridstruct", "spreadsheet");

            double[] gridx = new double[192];
            for (int i = 0; i < 192; i++) gridx[i] = -0.6 + i * (3.6 / 191.0);
            double[] gridy = new double[64];
            for (int i = 0; i < 64; i++) gridy[i] = -2.1 + i * (1.2 / 63.0);
            double[] gridz = new double[96];
            for (int i = 0; i < 96; i++) gridz[i] = -0.9 + i * (1.8 / 95.0);

            exporter.set("gridx3", gridx);
            exporter.set("gridy3", gridy);
            exporter.set("gridz3", gridz);
            exporter.set("header", "on");
            exporter.set("fullprec", "on");
            exporter.set("includecoords", true);
            exporter.set("includenan", true);

            if (solnum != null && solnum.contains(",")) {
                String[] snums = solnum.split(",");
                for (int s = 0; s < snums.length; s++) {
                    String sn = snums[s].trim();
                    exporter.set("solnum", sn);
                    exporter.set("filename", outputTxtPath + ".step_" + sn);
                    exporter.run();
                }
            } else {
                if (solnum != null && !solnum.trim().isEmpty() && !solnum.equalsIgnoreCase("auto")) {
                    exporter.set("solnum", solnum.trim());
                }
                exporter.set("filename", outputTxtPath);
                exporter.run();
            }

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"export_c04_volume\",\n");
            sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"output_txt\": ").append(quote(outputTxtPath)).append(",\n");
            sb.append("  \"dataset\": ").append(quote(targetDset)).append(",\n");
            sb.append("  \"solnum\": ").append(quote(solnum != null ? solnum : "auto")).append(",\n");
            sb.append("  \"nx\": 192,\n");
            sb.append("  \"ny\": 96,\n");
            sb.append("  \"nz\": 64\n");
            sb.append("}\n");
            emitResult(sb.toString());
        } catch (Throwable t) {
            outputError("EXPORT_C04_VOLUME_FAILED", "Failed to export C04 volume: " + t.getMessage(), getStackTrace(t));
        }
    }

    private static void handleExportSolutionData(String modelPath, String outputTxtPath) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            outputError("COMSOL_CONNECTION_FAILED", "Failed to load model for export: " + t.getMessage(), getStackTrace(t));
            return;
        }

        try {
            Object dataExp = model.result().export().create("data_export_s0", "Data");
            dataExp.getClass().getMethod("set", String.class, String[].class).invoke(dataExp, "expr", new String[]{"u", "v", "p", "vx-uy", "spf.U"});
            dataExp.getClass().getMethod("set", String.class, String.class).invoke(dataExp, "filename", outputTxtPath);
            dataExp.getClass().getMethod("run").invoke(dataExp);

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"export_solution_data\",\n");
            sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"output_txt\": ").append(quote(outputTxtPath)).append("\n");
            sb.append("}\n");
            emitResult(sb.toString());
        } catch (Throwable t) {
            outputError("SOLVER_FAILED", "Failed to export solution data: " + t.getMessage(), getStackTrace(t));
        }
    }

    /** Restart only from the native finite-element vector, never from sampled tensors. */
    private static void handlePrepareBranchState(String sourcePath, String outputPath, String timeText) {
        String stage = "validate", sourceVector = "null", seedVector = "null", variablesProbe = "null";
        String sourceDerivative = "null", seedDerivative = "null", probeDerivative = "null";
        String sourceMapping = "null", initializedMapping = "null", initializationDifference = "null";
        try {
            branchDistinctPaths(sourcePath, outputPath);
            double tb = Double.parseDouble(timeText);
            if (tb != 180.0) throw new IllegalArgumentException("Frozen experiment requires branch time 180 s");
            stage = "load source";
            Model model = ModelUtil.loadCopy("Model", sourcePath);
            String[] tags = branchTags(model);
            SolverSequence sol = model.sol(tags[6]);
            branchCheckTimeSettings(sol.feature(tags[8]));
            String before = branchSnapshot(model, tags), meshBefore = branchMeshFingerprint(model, tags);
            double[] times = sol.getPVals();
            int sourceIndex = branchExactTime(times, tb);
            if (sourceIndex != 901) throw new IllegalStateException("Frozen M3 source index must be 901");
            sourceVector = branchVectorJson(sol, sourceIndex);
            sourceDerivative = branchDerivativeJson(sol, sourceIndex);
            double[] nativeU = sol.getU(sourceIndex);
            double[] nativeUDot = sol.getUDot(sourceIndex);
            sourceMapping = branchDofMapping(sol);
            String sourceHash = branchHash(branchVectorBytes(nativeU));
            String derivativeHash = branchHash(branchVectorBytes(nativeUDot));
            if (nativeUDot.length != nativeU.length || times.length != 1001) throw new IllegalStateException("Unexpected native source derivative/history size");
            for (String name : model.param().varnames()) if (name.startsWith("branch_")) {
                throw new IllegalArgumentException("Source already contains branch parameters: " + name);
            }
            if (Arrays.asList(model.sol().tags()).contains("sol_branch_seed")) {
                throw new IllegalArgumentException("Source already contains sol_branch_seed");
            }
            stage = "copy full native solution history";
            // Copy COMSOL's complete native solution before clearing the working
            // solver. Reconstructing a solution from U alone loses native UDot.
            sol.copySolution("sol_branch_seed");
            SolverSequence seed = model.sol("sol_branch_seed");
            seed.label("Native M3 history; branch seed is saved frame 901 at t=180 s");
            seed.comments("source_solution=" + tags[6] + ";source_solnum=" + sourceIndex
                + ";source_time_s=" + tb + ";float64_le_sha256=" + sourceHash
                + ";udot_float64_le_sha256=" + derivativeHash + ";copy_method=copySolution_full_native_history");
            branchRequireVector(seed, sourceIndex, tb, sourceHash, nativeU.length);
            branchRequireDerivative(seed, sourceIndex, derivativeHash, nativeU.length);
            if (!Arrays.equals(times, seed.getPVals())) throw new IllegalStateException("Full native copy changed saved times");
            seedVector = branchVectorJson(seed, sourceIndex);
            seedDerivative = branchDerivativeJson(seed, sourceIndex);
            sol.clearSolutionData();
            model.param().set("branch_tb", "180[s]", "Frozen native restart time");
            model.param().set("branch_source_solnum", Integer.toString(sourceIndex), "Original M3 saved solution index");
            model.param().set("branch_seed_solnum", Integer.toString(sourceIndex), "Exact saved index in full native seed copy");
            stage = "configure native initialization";
            branchConfigureInitialization(model, tags);
            String configured = branchSnapshot(model, tags);
            stage = "Variables initialization only (no Time solve)";
            sol.run(tags[7], false);
            variablesProbe = branchVectorJson(sol, 1);
            probeDerivative = branchDerivativeJson(sol, 1);
            initializedMapping = branchDofMapping(sol);
            if (!sourceMapping.equals(initializedMapping)) throw new IllegalStateException("Native DOF field/coordinate/ordering changed during Variables initialization");
            initializationDifference = branchFieldDifferences(sol, nativeU, sol.getU(1));
            // This is an auditable initialization probe, not a reconstruction of
            // the original solution and not a claim about hidden Time input.
            branchRequireVector(seed, sourceIndex, tb, sourceHash, nativeU.length);
            branchRequireDerivative(seed, sourceIndex, derivativeHash, nativeU.length);
            String meshAfter = branchMeshFingerprint(model, tags);
            if (!meshBefore.equals(meshAfter)) throw new IllegalStateException("Native mesh changed during initialization");
            branchCheckTimeSettings(sol.feature(tags[8]));
            stage = "save common checkpoint";
            model.save(outputPath);
            emitResult("{\"status\":\"SUCCESS\",\"operation\":\"prepare_branch_state\",\"source_model\":"
                + quote(sourcePath) + ",\"output_model\":" + quote(outputPath)
                + ",\"time_solver_executed\":false,\"source_solnum\":" + sourceIndex
                + ",\"seed_solnum\":" + sourceIndex + ",\"seed_frame_count\":" + seed.getPVals().length
                + ",\"seed_times\":" + doubleArrayToJson(seed.getPVals())
                + ",\"native_copy_method\":\"copySolution_full_native_history\""
                + ",\"seed_solution_tag\":\"sol_branch_seed\",\"dataset_tag\":" + quote(tags[9])
                + ",\"solution_tag\":" + quote(tags[6]) + ",\"source_times\":" + doubleArrayToJson(times)
                + ",\"seed_provenance\":" + quote(seed.comments())
                + ",\"native_vectors\":{\"source\":" + sourceVector + ",\"seed\":" + seedVector
                + ",\"variables_probe\":" + variablesProbe
                + "},\"native_derivatives\":{\"source\":" + sourceDerivative + ",\"seed\":" + seedDerivative
                + ",\"variables_probe\":" + probeDerivative
                + "},\"source_dof_mapping\":" + sourceMapping + ",\"initialized_dof_mapping\":" + initializedMapping
                + ",\"initialization_difference_by_field\":" + initializationDifference + ",\"mesh_before\":" + meshBefore
                + ",\"mesh_after\":" + meshAfter + ",\"before\":" + before
                + ",\"configured\":" + configured + ",\"after\":" + branchSnapshot(model, tags) + "}");
        } catch (Throwable t) {
            emitResult("{\"status\":\"ERROR\",\"error_code\":\"MODEL_VALIDATION_FAILED\",\"message\":"
                + quote("Native branch preparation failed at " + stage + ": " + t.getMessage())
                + ",\"details\":" + quote(getStackTrace(t)) + ",\"partial_native_vectors\":{\"source\":"
                + sourceVector + ",\"seed\":" + seedVector + ",\"variables_probe\":" + variablesProbe
                + "},\"partial_native_derivatives\":{\"source\":" + sourceDerivative + ",\"seed\":" + seedDerivative
                + ",\"variables_probe\":" + probeDerivative
                + "},\"source_dof_mapping\":" + sourceMapping + ",\"initialized_dof_mapping\":" + initializedMapping
                + ",\"initialization_difference_by_field\":" + initializationDifference + "}");
        }
    }

    /** The only PDE solve added for this experiment: a new branch from the common checkpoint. */
    private static void handleRunPulseBranch(String sourcePath, String outputPath, String amplitudeText, String restartProtocolId) {
        handleRunPulseBranch(sourcePath, outputPath, amplitudeText, restartProtocolId, null);
    }

    private static void handleRunPulseBranch(String sourcePath, String outputPath, String amplitudeText, String restartProtocolId, String seriesId) {
        String stage = "validate", seedVector = "null", inputVector = "null", variablesProbe = "null";
        String seedDerivative = "null", inputDerivative = "null", probeDerivative = "null";
        try {
            branchDistinctPaths(sourcePath, outputPath);
            if (!"native-restart-consistent-off-v2".equals(restartProtocolId)) {
                throw new IllegalArgumentException("Explicit frozen restart protocol native-restart-consistent-off-v2 is required");
            }
            double amplitude = Double.parseDouble(amplitudeText);
            if (seriesId == null) {
                if (amplitude != 0.0 && amplitude != 0.005) {
                    throw new IllegalArgumentException("Frozen experiment only permits acceleration 0 or 0.005 m/s^2");
                }
            } else {
                if (!"m3-pulse-amplitude-series-v1".equals(seriesId)) {
                    throw new IllegalArgumentException("Explicit series m3-pulse-amplitude-series-v1 is required");
                }
                if (amplitude != 0.0025 && amplitude != 0.010) {
                    throw new IllegalArgumentException("Frozen amplitude series only permits acceleration 0.0025 or 0.010 m/s^2");
                }
            }
            stage = "load common checkpoint";
            Model model = ModelUtil.loadCopy("Model", sourcePath);
            String[] tags = branchTags(model);
            SolverSequence sol = model.sol(tags[6]), seed = model.sol("sol_branch_seed");
            if (model.param().evaluate("branch_tb") != 180.0 || model.param().evaluate("branch_source_solnum") != 901) {
                throw new IllegalStateException("Checkpoint lacks the frozen original state mapping");
            }
            int seedIndex = (int) model.param().evaluate("branch_seed_solnum");
            if (seedIndex != 901 || seed.getPVals().length != 1001 || branchExactTime(seed.getPVals(), 180) != seedIndex) {
                throw new IllegalStateException("Checkpoint does not preserve the full native source history");
            }
            String before = branchSnapshot(model, tags), meshBefore = branchMeshFingerprint(model, tags);
            seedVector = branchVectorJson(seed, seedIndex);
            seedDerivative = branchDerivativeJson(seed, seedIndex);
            double[] nativeU = seed.getU(seedIndex);
            String sourceMapping = branchDofMapping(sol);
            String seedHash = branchHash(branchVectorBytes(nativeU));
            String derivativeHash = branchHash(branchVectorBytes(seed.getUDot(seedIndex)));
            if (!seed.comments().contains("float64_le_sha256=" + seedHash)
                    || !seed.comments().contains("udot_float64_le_sha256=" + derivativeHash)
                    || !seed.comments().contains("copy_method=copySolution_full_native_history")) {
                throw new IllegalStateException("Stored seed provenance and actual native state hashes disagree");
            }
            branchRequireVector(seed, seedIndex, 180, seedHash, nativeU.length);
            branchRequireDerivative(seed, seedIndex, derivativeHash, nativeU.length);
            branchCheckTimeSettings(sol.feature(tags[8]));
            stage = "install preregistered force";
            ModelParam p = model.param();
            String[] parameterNames = {"branch_A", "branch_xc", "branch_yc", "branch_sigma", "branch_on", "branch_off"};
            String[] parameterValues = {Double.toString(amplitude) + "[m/s^2]", "0.15[m]", "0.12[m]", "0.01[m]", "181[s]", "183[s]"};
            for (int i = 0; i < parameterNames.length; i++) {
                if (Arrays.asList(p.varnames()).contains(parameterNames[i])) throw new IllegalStateException("Checkpoint contains pulse parameter " + parameterNames[i]);
                p.set(parameterNames[i], parameterValues[i]);
            }
            com.comsol.model.physics.Physics physics = model.component(tags[0]).physics(tags[3]);
            if (Arrays.asList(physics.feature().tags()).contains("branch_vf")) throw new IllegalStateException("Pulse force already exists");
            com.comsol.model.physics.PhysicsFeature force = physics.create("branch_vf", "VolumeForce", 2);
            force.selection().set(new int[]{1});
            String fy = "rho_f*branch_A*exp(-((x-branch_xc)^2+(y-branch_yc)^2)/(2*branch_sigma^2))"
                + "*if((t>branch_on)&&(t<branch_off),sin(pi*(t-branch_on)/(branch_off-branch_on))^2,0)";
            force.set("F", new String[]{"0[N/m^3]", fy, "0[N/m^3]"});
            String[] actualForce = force.getStringArray("F");
            if (actualForce.length != 3 || !actualForce[1].equals(fy)
                    || !Arrays.equals(force.selection().entities(), new int[]{1}) || !force.isActive()) {
                throw new IllegalStateException("Actual VolumeForce expression or domain differs from frozen plan");
            }
            branchConfigureInitialization(model, tags);
            // Versioned restart protocol v2 changes only this initialization
            // property; the original checkpoint and before snapshot retain BWE.
            SolverFeature branchTime = sol.feature(tags[8]);
            if (!"bweuler".equals(branchTime.getString("consistent"))) {
                throw new IllegalStateException("Common checkpoint initialization is not the recorded bweuler baseline");
            }
            branchTime.set("consistent", "off");
            if (!"off".equals(branchTime.getString("consistent"))) {
                throw new IllegalStateException("Restart protocol consistent=off did not read back");
            }
            // Read the actual native state selected by the live initialization
            // settings. The working solver's prior Variables probe is not input.
            int selectedIndex = branchManualSolutionIndex(sol.feature(tags[7]));
            SolverSequence selectedSource = model.sol(sol.feature(tags[7]).getString("initsol"));
            inputVector = branchVectorJson(selectedSource, selectedIndex);
            inputDerivative = branchDerivativeJson(selectedSource, selectedIndex);
            branchRequireVector(selectedSource, selectedIndex, 180, seedHash, nativeU.length);
            branchRequireDerivative(selectedSource, selectedIndex, derivativeHash, nativeU.length);
            stage = "check actual branch initialization";
            sol.run(tags[7], false);
            variablesProbe = branchVectorJson(sol, 1);
            probeDerivative = branchDerivativeJson(sol, 1);
            String initializedMapping = branchDofMapping(sol);
            if (!sourceMapping.equals(initializedMapping)) throw new IllegalStateException("Native DOF mapping changed when adding VolumeForce");
            String initializationDifference = branchFieldDifferences(sol, nativeU, sol.getU(1));
            double[] initializedTime = sol.getPVals(1);
            if (initializedTime.length != 1 || initializedTime[0] != 180 || sol.getU(1).length != nativeU.length) {
                throw new IllegalStateException("Actual Variables initial time/DOF count differs from the native source");
            }
            // Variables has now installed the new parameter context in this solution.
            // Numerical result evaluation reads it without replacing the solution.
            String forceInitial = branchForceEvidence(model, tags, actualForce, new double[]{180}, "branch_pre");
            String configured = branchSnapshot(model, tags);
            if (!meshBefore.equals(branchMeshFingerprint(model, tags))) throw new IllegalStateException("Native mesh changed before Time solver");
            branchCheckTimeSettings(sol.feature(tags[8]));
            stage = "Time solve 180..200 s";
            if (!"off".equals(branchTime.getString("consistent"))) throw new IllegalStateException("Restart protocol changed before Time");
            // The prior Variables run is a validation probe. runAll executes
            // Compile -> Variables -> Time together and initializes again from
            // the same live native seed; do not label the probe as its hidden input.
            variablesProbe = branchVectorJson(sol, 1);
            SolverFeature vars = sol.feature(tags[7]);
            System.out.println("BRANCH_RESTART_PROTOCOL_READBACK={\"restart_protocol_id\":" + quote(restartProtocolId)
                + (seriesId == null ? "" : ",\"series_id\":" + quote(seriesId))
                + ",\"consistent\":" + quote(branchTime.getString("consistent")) + "}");
            System.out.println("BRANCH_NATIVE_INIT_READBACK={\"control\":" + quote(vars.getString("control"))
                + ",\"initmethod\":" + quote(vars.getString("initmethod"))
                + ",\"initsol\":" + quote(vars.getString("initsol"))
                + ",\"initsoluse\":" + quote(vars.getString("initsoluse"))
                + ",\"solnum\":" + quote(vars.getString("solnum"))
                + ",\"manualsolnum\":" + nativeValueJson(nativeReadProperty(vars, "manualsolnum"))
                + ",\"native_seed_time\":" + doubleArrayToJson(seed.getPVals(seedIndex))
                + ",\"native_seed_sha256\":" + quote(branchHash(branchVectorBytes(seed.getU(seedIndex))))
                + ",\"native_seed_udot_sha256\":" + quote(branchHash(branchVectorBytes(seed.getUDot(seedIndex)))) + "}");
            System.out.println("BRANCH_TIME_START method=runAll amplitude_m_s2=" + amplitude + " native_source_sha256=" + seedHash
                + " variables_probe_sha256=" + branchHash(branchVectorBytes(sol.getU(1))));
            sol.runAll(false);
            System.out.println("BRANCH_TIME_FINISHED");
            stage = "check saved branch results";
            double[] times = sol.getPVals();
            if (times.length != 101) throw new IllegalStateException("Branch must save exactly 101 output times");
            for (int i = 0; i < times.length; i++) {
                if (!Double.isFinite(times[i]) || Math.abs(times[i] - (180 + 0.2 * i)) > 1e-10) {
                    throw new IllegalStateException("Branch output time schedule changed");
                }
                branchVectorBytes(sol.getU(i + 1)); // finite full native vector, every saved frame
            }
            String firstVector = branchVectorJson(sol, 1);
            String firstDerivative = branchDerivativeJson(sol, 1);
            String forceEvidence = branchForceEvidence(model, tags, actualForce, times, "branch_post");
            String meshAfter = branchMeshFingerprint(model, tags);
            if (!meshBefore.equals(meshAfter)) throw new IllegalStateException("Native mesh changed during branch solve");
            branchCheckTimeSettings(sol.feature(tags[8]));
            if (!"off".equals(branchTime.getString("consistent"))) throw new IllegalStateException("Restart protocol changed during Time");
            stage = "save independent branch solution";
            model.save(outputPath);
            StringBuilder feature = new StringBuilder();
            appendNativeFeature(feature, force, tags[0] + "/" + tags[3] + "/" + force.tag());
            emitResult("{\"status\":\"SUCCESS\",\"operation\":\"run_pulse_branch\",\"source_model\":" + quote(sourcePath)
                + ",\"output_model\":" + quote(outputPath) + ",\"time_solver_executed\":true,\"amplitude_m_s2\":" + amplitude
                + ",\"restart_protocol_id\":" + quote(restartProtocolId)
                + (seriesId == null ? "" : ",\"series_id\":" + quote(seriesId))
                + ",\"dataset_tag\":" + quote(tags[9]) + ",\"solution_tag\":" + quote(tags[6])
                + ",\"seed_solnum\":" + seedIndex + ",\"seed_frame_count\":" + seed.getPVals().length
                + ",\"seed_times\":" + doubleArrayToJson(seed.getPVals())
                + ",\"native_copy_method\":\"copySolution_full_native_history\""
                + ",\"seed_provenance\":" + quote(seed.comments()) + ",\"actual_times\":" + doubleArrayToJson(times)
                + ",\"initialization_path\":\"native seed -> runAll(Compile, Variables, Time); prior Variables execution is a validation probe only\""
                + ",\"native_input_role\":\"full native seed frame selected by live Variables initsol/manualsolnum; variables_probe is not claimed to be the internal Time input\""
                + ",\"native_vectors\":{\"seed\":" + seedVector + ",\"input\":" + inputVector
                + ",\"variables_probe\":" + variablesProbe + ",\"solver_first\":" + firstVector
                + "},\"native_derivatives\":{\"seed\":" + seedDerivative + ",\"input\":" + inputDerivative
                + ",\"variables_probe\":" + probeDerivative + ",\"solver_first\":" + firstDerivative
                + "},\"source_dof_mapping\":" + sourceMapping + ",\"initialized_dof_mapping\":" + initializedMapping
                + ",\"initialization_difference_by_field\":" + initializationDifference
                + ",\"force_feature\":" + feature + ",\"force_initial_evaluation\":" + forceInitial
                + ",\"force_evaluation\":" + forceEvidence + ",\"mesh_before\":" + meshBefore + ",\"mesh_after\":" + meshAfter
                + ",\"before\":" + before + ",\"configured\":" + configured + ",\"after\":" + branchSnapshot(model, tags)
                + ",\"time_solver_log\":" + quote(sol.feature(tags[8]).getString("message")) + "}");
        } catch (Throwable t) {
            emitResult("{\"status\":\"ERROR\",\"error_code\":\"MODEL_VALIDATION_FAILED\",\"message\":"
                + quote("Pulse branch failed at " + stage + ": " + t.getMessage()) + ",\"details\":" + quote(getStackTrace(t))
                + ",\"restart_protocol_id\":" + quote(restartProtocolId)
                + (seriesId == null ? "" : ",\"series_id\":" + quote(seriesId))
                + ",\"partial_native_vectors\":{\"seed\":" + seedVector + ",\"input\":" + inputVector
                + ",\"variables_probe\":" + variablesProbe
                + "},\"partial_native_derivatives\":{\"seed\":" + seedDerivative + ",\"input\":" + inputDerivative
                + ",\"variables_probe\":" + probeDerivative + "}}");
        }
    }

    static void branchDistinctPaths(String source, String output) {
        String a = java.nio.file.Paths.get(source).toAbsolutePath().normalize().toString();
        String b = java.nio.file.Paths.get(output).toAbsolutePath().normalize().toString();
        if (a.equalsIgnoreCase(b)) throw new IllegalArgumentException("Source and output must differ");
        // Python owns pre-creation, no-overwrite admission, and source file hashes.
    }

    /** Resolve associations from the live M3 nodes already inspected before this experiment. */
    static String[] branchTags(Model model) {
        DatasetFeature dataset = model.result().dataset("dset1");
        if (!"Solution".equals(dataset.getType())) throw new IllegalStateException("dset1 is not a Solution dataset");
        String component = dataset.getString("comp"), solution = dataset.getString("solution");
        ModelNode comp = model.component(component);
        if (comp.geom().tags().length != 1 || comp.physics().tags().length != 1) throw new IllegalStateException("Ambiguous branch geometry/physics");
        String geometry = comp.geom().tags()[0], physics = comp.physics().tags()[0];
        if (comp.geom(geometry).getSDim() != 2 || comp.geom(geometry).getNDomains() != 1
                || !"m".equals(comp.geom(geometry).lengthUnit())) throw new IllegalStateException("Frozen branch requires one 2D fluid domain in metres");
        SolverSequence sol = model.sol(solution);
        String study = null, step = null, vars = null, time = null;
        for (String tag : sol.feature().tags()) {
            SolverFeature feature = sol.feature(tag);
            if ("StudyStep".equals(feature.getType())) { study = feature.getString("study"); step = feature.getString("studystep"); }
            else if ("Variables".equals(feature.getType())) { if (vars != null) throw new IllegalStateException("Multiple Variables features"); vars = tag; }
            else if ("Time".equals(feature.getType())) { if (time != null) throw new IllegalStateException("Multiple Time features"); time = tag; }
            else throw new IllegalStateException("Unexpected top-level branch solver feature " + feature.getType());
        }
        if (study == null || step == null || vars == null || time == null) throw new IllegalStateException("Missing branch solver associations");
        String mesh = null;
        String[] meshPairs = model.study(study).feature(step).getStringArray("mesh");
        if (meshPairs.length % 2 != 0) throw new IllegalStateException("Invalid actual study mesh association");
        for (int i = 0; i < meshPairs.length; i += 2) if (geometry.equals(meshPairs[i])) {
            if (mesh != null) throw new IllegalStateException("Ambiguous actual study mesh association");
            mesh = meshPairs[i + 1];
        }
        if (mesh == null || !Arrays.asList(comp.mesh().tags()).contains(mesh)
                || !geometry.equals(comp.mesh(mesh).geom())) throw new IllegalStateException("Actual study mesh association is absent or invalid");
        return new String[]{component, geometry, mesh, physics, study, step, solution, vars, time, dataset.tag()};
    }

    static int branchExactTime(double[] times, double wanted) {
        int index = -1;
        for (int i = 0; i < times.length; i++) {
            if (!Double.isFinite(times[i])) throw new IllegalStateException("Nonfinite saved solution time");
            if (Math.abs(times[i] - wanted) <= 1e-12) {
                if (index >= 0) throw new IllegalStateException("Ambiguous native saved time");
                index = i + 1;
            }
        }
        if (index < 0) throw new IllegalStateException("Required native saved time is absent");
        return index;
    }

    static void branchConfigureInitialization(Model model, String[] tags) {
        model.study(tags[4]).feature(tags[5]).set("tlist", "range(180,0.2,200)");
        SolverFeature time = model.sol(tags[6]).feature(tags[8]);
        time.set("tlist", "range(180,0.2,200)");
        SolverFeature vars = model.sol(tags[6]).feature(tags[7]);
        vars.set("control", "user");
        // This live 6.4 Variables node exposes direct user-controlled initmethod,
        // not the study-level useinitsol property. Verify the actual initial vector.
        vars.set("initmethod", "sol");
        vars.set("initsol", "sol_branch_seed");
        vars.set("initsoluse", "current");
        vars.set("solnum", "manual");
        int seedIndex = (int) model.param().evaluate("branch_seed_solnum");
        if (seedIndex != 901) throw new IllegalStateException("Frozen full native seed index must be 901");
        vars.set("manualsolnum", seedIndex);
        vars.set("cname", new String[]{"t", "timestep"});
        vars.set("clist", new String[]{"180", "0.2"});
        if (!"user".equals(vars.getString("control")) || !"sol".equals(vars.getString("initmethod"))
                || !"sol_branch_seed".equals(vars.getString("initsol")) || !"manual".equals(vars.getString("solnum"))
                || branchManualSolutionIndex(vars) != seedIndex) {
            throw new IllegalStateException("Native initialization configuration did not read back");
        }
    }

    static int branchManualSolutionIndex(SolverFeature vars) {
        double[] indices = vars.getDoubleArray("manualsolnum");
        if (indices.length != 1 || !Double.isFinite(indices[0]) || indices[0] != Math.rint(indices[0])) {
            throw new IllegalStateException("Actual Variables initialization must select one saved index");
        }
        return (int) indices[0];
    }

    static void branchCheckTimeSettings(SolverFeature time) {
        if (Math.abs(time.getDouble("rtol") - 1e-4) > 1e-15
                || !"const".equals(time.getString("maxstepconstraintbdf"))
                || Math.abs(time.getDouble("maxstepbdf") - 0.05) > 1e-15) {
            throw new IllegalStateException("Frozen solver rtol/maxstep settings are not present");
        }
    }

    static byte[] branchVectorBytes(double[] values) {
        java.nio.ByteBuffer bytes = java.nio.ByteBuffer.allocate(values.length * 8).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        for (double value : values) {
            if (!Double.isFinite(value)) throw new IllegalStateException("Nonfinite native degree of freedom");
            bytes.putDouble(value);
        }
        return bytes.array();
    }

    static String branchHash(byte[] bytes) {
        try {
            byte[] digest = java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
            StringBuilder result = new StringBuilder();
            for (byte value : digest) result.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            return result.toString();
        } catch (java.security.NoSuchAlgorithmException e) { throw new IllegalStateException(e); }
    }

    static String branchVectorJson(SolverSequence sol, int index) {
        double[] values = sol.getU(index), time = sol.getPVals(index);
        if (time.length != 1 || !Double.isFinite(time[0]) || !sol.isRealU(index)) throw new IllegalStateException("Invalid native real transient state");
        byte[] bytes = branchVectorBytes(values);
        return "{\"solution_tag\":" + quote(sol.tag()) + ",\"solnum\":" + index + ",\"time\":" + time[0]
            + ",\"count\":" + values.length + ",\"solution_size\":" + intArrayToJson(sol.getSize())
            + ",\"dtype\":\"float64\",\"byte_order\":\"little\",\"sha256\":" + quote(branchHash(bytes))
            + ",\"base64\":" + quote(java.util.Base64.getEncoder().encodeToString(bytes)) + "}";
    }

    /** Preserve undefined derivative entries explicitly; never replace them by zero. */
    static String branchDerivativeJson(SolverSequence sol, int index) {
        try {
            double[] values = sol.getUDot(index), times = sol.getPVals(index);
            java.nio.ByteBuffer bytes = java.nio.ByteBuffer.allocate(values.length * 8).order(java.nio.ByteOrder.LITTLE_ENDIAN);
            int finite = 0, nan = 0, positiveInfinity = 0, negativeInfinity = 0;
            for (double value : values) {
                bytes.putDouble(value);
                if (Double.isFinite(value)) finite++;
                else if (Double.isNaN(value)) nan++;
                else if (value > 0) positiveInfinity++;
                else negativeInfinity++;
            }
            return "{\"available\":true,\"solution_tag\":" + quote(sol.tag()) + ",\"solnum\":" + index
                + ",\"time\":" + times[0] + ",\"count\":" + values.length + ",\"finite_count\":" + finite
                + ",\"nan_count\":" + nan + ",\"positive_infinity_count\":" + positiveInfinity
                + ",\"negative_infinity_count\":" + negativeInfinity + ",\"dtype\":\"float64\",\"byte_order\":\"little\",\"sha256\":"
                + quote(branchHash(bytes.array())) + ",\"base64\":" + quote(java.util.Base64.getEncoder().encodeToString(bytes.array())) + "}";
        } catch (Throwable t) {
            return "{\"available\":false,\"solution_tag\":" + quote(sol.tag()) + ",\"solnum\":" + index
                + ",\"read_error\":" + quote(t.getMessage()) + ",\"details\":" + quote(getStackTrace(t)) + "}";
        }
    }

    static void branchRequireVector(SolverSequence sol, int index, double time, String hash, int count) {
        double[] values = sol.getU(index), actualTime = sol.getPVals(index);
        if (actualTime.length != 1 || Math.abs(actualTime[0] - time) > 1e-12 || values.length != count
                || !branchHash(branchVectorBytes(values)).equals(hash)) {
            throw new IllegalStateException("Actual native vector/time differs from original branch state: " + sol.tag());
        }
    }

    static void branchRequireDerivative(SolverSequence sol, int index, String hash, int count) {
        double[] values = sol.getUDot(index);
        if (values.length != count || !branchHash(branchVectorBytes(values)).equals(hash)) {
            throw new IllegalStateException("Actual native time derivative differs from original branch state: " + sol.tag());
        }
    }

    /** Public XmeshInfo maps every named DOF to its native solution-vector index. */
    static String branchDofMapping(SolverSequence sol) {
        XmeshInfo info = sol.xmeshInfo();
        XmeshInfoDofs dofs = info.dofs();
        String payload = "{\"n_dofs\":" + info.nDofs() + ",\"field_names\":" + stringArrayToJson(info.fieldNames())
            + ",\"field_dof_counts\":" + intArrayToJson(info.fieldNDofs()) + ",\"dof_names\":" + stringArrayToJson(dofs.dofNames())
            + ",\"name_indices\":" + intArrayToJson(dofs.nameInds()) + ",\"solution_vector_indices\":" + intArrayToJson(dofs.solVectorInds())
            + ",\"geometry_indices\":" + intArrayToJson(dofs.geomNums()) + ",\"coordinates\":" + branchCoordinateBytes(dofs.coords()) + "}";
        return "{\"sha256\":" + quote(branchHash(payload.getBytes(java.nio.charset.StandardCharsets.UTF_8)))
            + ",\"hash_encoding\":\"UTF-8 exact JSON payload below\",\"payload\":" + payload + "}";
    }

    private static String branchCoordinateBytes(double[][] coordinates) {
        int columns = coordinates.length == 0 ? 0 : coordinates[0].length, nonfinite = 0;
        java.nio.ByteBuffer bytes = java.nio.ByteBuffer.allocate(coordinates.length * columns * 8).order(java.nio.ByteOrder.LITTLE_ENDIAN);
        for (double[] row : coordinates) {
            if (row.length != columns) throw new IllegalStateException("Ragged native DOF coordinate array");
            for (double value : row) {
                if (!Double.isFinite(value)) nonfinite++;
                bytes.putDouble(value); // Internal/global DOFs may have no spatial coordinate.
            }
        }
        return "{\"shape\":[" + coordinates.length + "," + columns + "],\"dtype\":\"float64\",\"byte_order\":\"little\",\"order\":\"C\",\"non_finite_count\":" + nonfinite
            + ",\"sha256\":" + quote(branchHash(bytes.array())) + ",\"base64\":" + quote(java.util.Base64.getEncoder().encodeToString(bytes.array())) + "}";
    }

    static String branchFieldDifferences(SolverSequence sol, double[] source, double[] initialized) {
        if (source.length != initialized.length) throw new IllegalStateException("Native vector length changed");
        XmeshInfoDofs dofs = sol.xmeshInfo().dofs();
        String[] names = dofs.dofNames();
        int[] fields = dofs.nameInds(), indices = dofs.solVectorInds();
        int[] counts = new int[names.length], different = new int[names.length];
        double[] maxDifference = new double[names.length], sourceMin = new double[names.length], sourceMax = new double[names.length];
        double[] initializedMin = new double[names.length], initializedMax = new double[names.length];
        Arrays.fill(sourceMin, Double.POSITIVE_INFINITY); Arrays.fill(initializedMin, Double.POSITIVE_INFINITY);
        Arrays.fill(sourceMax, Double.NEGATIVE_INFINITY); Arrays.fill(initializedMax, Double.NEGATIVE_INFINITY);
        for (int i = 0; i < indices.length; i++) {
            int index = indices[i], field = fields[i];
            if (index < 0 || index >= source.length || field < 0 || field >= names.length) throw new IllegalStateException("Invalid actual DOF mapping index");
            counts[field]++;
            if (Double.doubleToLongBits(source[index]) != Double.doubleToLongBits(initialized[index])) different[field]++;
            maxDifference[field] = Math.max(maxDifference[field], Math.abs(source[index] - initialized[index]));
            sourceMin[field] = Math.min(sourceMin[field], source[index]); sourceMax[field] = Math.max(sourceMax[field], source[index]);
            initializedMin[field] = Math.min(initializedMin[field], initialized[index]); initializedMax[field] = Math.max(initializedMax[field], initialized[index]);
        }
        StringBuilder result = new StringBuilder("[");
        for (int field = 0; field < names.length; field++) {
            if (field > 0) result.append(",");
            result.append("{\"name\":").append(quote(names[field])).append(",\"count\":").append(counts[field])
              .append(",\"different_count\":").append(different[field]).append(",\"max_absolute_difference\":").append(maxDifference[field])
              .append(",\"source_range\":").append(doubleArrayToJson(new double[]{sourceMin[field], sourceMax[field]}))
              .append(",\"initialized_range\":").append(doubleArrayToJson(new double[]{initializedMin[field], initializedMax[field]})).append("}");
        }
        return result.append("]").toString();
    }

    static String branchMeshFingerprint(Model model, String[] tags) {
        MeshSequence mesh = model.component(tags[0]).mesh(tags[2]);
        StringBuilder data = new StringBuilder();
        data.append(nativeValueJson(mesh.getVertex()));
        String[] types = mesh.getTypes(); Arrays.sort(types);
        for (String type : types) data.append(type).append(nativeValueJson(mesh.getElem(type))).append(nativeValueJson(mesh.getElemEntity(type)));
        for (String tag : mesh.feature().tags()) appendNativeFeature(data, mesh.feature(tag), tag);
        return "{\"component_tag\":" + quote(tags[0]) + ",\"geometry_tag\":" + quote(tags[1]) + ",\"mesh_tag\":" + quote(tags[2])
            + ",\"vertex_count\":" + mesh.getNumVertex() + ",\"element_count\":" + mesh.getNumElem()
            + ",\"sha256\":" + quote(branchHash(data.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8)))
            + ",\"hash_input\":\"ordered native vertices, sorted element-type connectivity/entity arrays, mesh feature tree; UTF-8 JSON values\"}";
    }

    static String branchSnapshot(Model model, String[] tags) {
        StringBuilder sb = new StringBuilder("{\"parameters\":{");
        String[] names = model.param().varnames(); Arrays.sort(names);
        for (int i = 0; i < names.length; i++) {
            if (i > 0) sb.append(",");
            sb.append(quote(names[i])).append(":{\"expr\":").append(quote(model.param().get(names[i])))
              .append(",\"value\":").append(model.param().evaluate(names[i]))
              .append(",\"unit\":").append(quote(model.param().evaluateUnit(names[i]))).append("}");
        }
        sb.append("},\"solver_features\":[");
        String[] features = model.sol(tags[6]).feature().tags();
        for (int i = 0; i < features.length; i++) {
            if (i > 0) sb.append(",");
            appendNativeFeature(sb, model.sol(tags[6]).feature(features[i]), tags[6] + "/" + features[i]);
        }
        sb.append("],\"study_step\":");
        appendNativeFeature(sb, model.study(tags[4]).feature(tags[5]), tags[4] + "/" + tags[5]);
        sb.append(",\"non_mesh\":"); appendNativeNonMesh(sb, model);
        return sb.append("}").toString();
    }

    /** Evaluate the exact expressions read from the real VolumeForce, never a nominal substitute. */
    private static String branchForceEvidence(Model model, String[] tags, String[] force, double[] times, String prefix) {
        int[] indices = new int[times.length]; for (int i = 0; i < indices.length; i++) indices[i] = i + 1;
        DatasetFeature point = model.result().dataset().create(prefix + "_point", "CutPoint2D");
        point.set("data", tags[9]); point.set("pointx", 0.15); point.set("pointy", 0.12);
        NumericalFeature evaluation = model.result().numerical().create(prefix + "_eval", "EvalPoint");
        evaluation.set("data", point.tag());
        evaluation.set("expr", new String[]{force[0], force[1], force[2], "t", "x", "y"});
        evaluation.set("solrepresentation", "solnum"); evaluation.set("innerinput", "manual"); evaluation.set("solnum", indices);
        double[][] raw = evaluation.getReal();
        double[][] values = branchResultMatrix(raw, 6, times.length);
        String[] units = evaluation.getStringArray("unit");
        if (units.length != 6 || !"N/m^3".equals(units[0]) || !"N/m^3".equals(units[1]) || !"N/m^3".equals(units[2])
                || !"s".equals(units[3]) || !"m".equals(units[4]) || !"m".equals(units[5])) {
            throw new IllegalStateException("Actual force-expression units are not N/m^3, s, m, m: " + Arrays.toString(units));
        }
        for (int i = 0; i < times.length; i++) {
            if (Math.abs(values[3][i] - times[i]) > 1e-10 || Math.abs(values[4][i] - 0.15) > 1e-12
                    || Math.abs(values[5][i] - 0.12) > 1e-12) throw new IllegalStateException("Force evaluation has wrong actual time/coordinates");
            if (times[i] <= 181 || times[i] >= 183) {
                if (values[0][i] != 0 || values[1][i] != 0 || values[2][i] != 0) throw new IllegalStateException("Force is nonzero outside frozen pulse window");
            }
        }
        NumericalFeature integral = model.result().numerical().create(prefix + "_integral", "IntSurface");
        integral.set("data", tags[9]); integral.selection().set(new int[]{1}); integral.set("expr", new String[]{force[1]});
        integral.set("solrepresentation", "solnum"); integral.set("innerinput", "manual"); integral.set("solnum", indices);
        double[][] totals = branchResultMatrix(integral.getReal(), 1, times.length);
        String[] integralUnits = integral.getStringArray("unit");
        if (integralUnits.length != 1 || !"N/m".equals(integralUnits[0])) throw new IllegalStateException("Actual source integral unit is not N/m");
        return "{\"expression_source_feature\":\"branch_vf\",\"dataset_tag\":" + quote(tags[9])
            + ",\"solution_tag\":" + quote(model.result().dataset(tags[9]).getString("solution"))
            + ",\"actual_times\":" + doubleArrayToJson(values[3])
            + ",\"point_evaluation\":{\"tag\":" + quote(evaluation.tag()) + ",\"type\":" + quote(evaluation.getType())
            + ",\"dataset_tag\":" + quote(evaluation.getString("data")) + ",\"point_source_dataset\":" + quote(point.getString("data"))
            + ",\"point_dataset_tag\":" + quote(point.tag()) + ",\"point_dataset_type\":" + quote(point.getType())
            + ",\"solrepresentation\":" + quote(evaluation.getString("solrepresentation")) + ",\"innerinput\":" + quote(evaluation.getString("innerinput"))
            + ",\"solnum\":" + intArrayToJson(evaluation.getIntArray("solnum")) + ",\"expressions\":" + stringArrayToJson(evaluation.getStringArray("expr"))
            + ",\"units\":" + stringArrayToJson(units) + ",\"values_by_expression\":" + nativeValueJson(values)
            + "},\"integral\":{\"tag\":" + quote(integral.tag()) + ",\"type\":" + quote(integral.getType())
            + ",\"dataset_tag\":" + quote(integral.getString("data")) + ",\"selection\":" + intArrayToJson(integral.selection().entities())
            + ",\"solrepresentation\":" + quote(integral.getString("solrepresentation")) + ",\"innerinput\":" + quote(integral.getString("innerinput"))
            + ",\"solnum\":" + intArrayToJson(integral.getIntArray("solnum")) + ",\"expressions\":" + stringArrayToJson(integral.getStringArray("expr"))
            + ",\"units\":" + stringArrayToJson(integralUnits) + ",\"Fy_per_depth\":" + doubleArrayToJson(totals[0]) + "}}";
    }

    static double[][] branchResultMatrix(double[][] matrix, int expressions, int times) {
        if (matrix.length != expressions) throw new IllegalStateException("Unexpected numerical expression row count");
        for (double[] row : matrix) {
            if (row.length != times) throw new IllegalStateException("Unexpected numerical time count");
            for (double value : row) if (!Double.isFinite(value)) throw new IllegalStateException("Nonfinite native numerical evaluation");
        }
        return matrix;
    }

    /** Shared sample/sequence-batch primitive; never solves or saves the loaded model. */
    private static void handleExportFullFieldSample(String modelPath, String outputDir, String timesCsv,
                                                    String nxText, String nyText, String xminText,
                                                    String xmaxText, String yminText, String ymaxText,
                                                    String datasetTag, boolean sequenceBatch) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            outputError("COMSOL_CONNECTION_FAILED", "Failed to load model for full-field sampling: " + t.getMessage(), getStackTrace(t));
            return;
        }
        try {
            int nx = Integer.parseInt(nxText), ny = Integer.parseInt(nyText);
            if (nx < 3 || ny < 3) throw new IllegalArgumentException("Grid dimensions must be at least 3x3");
            double xmin = Double.parseDouble(xminText), xmax = Double.parseDouble(xmaxText);
            double ymin = Double.parseDouble(yminText), ymax = Double.parseDouble(ymaxText);
            double[] bounds = new double[]{xmin, xmax, ymin, ymax};
            for (double value : bounds) if (!Double.isFinite(value)) throw new IllegalArgumentException("Non-finite grid bounds");
            if (!(xmax > xmin) || !(ymax > ymin)) throw new IllegalArgumentException("Invalid grid bounds");

            DatasetFeature dataset = model.result().dataset(datasetTag);
            if (!"Solution".equals(dataset.getType())) throw new IllegalArgumentException("Full-field sampling requires an actual Solution dataset");
            String solutionTag = dataset.getString("solution");
            String componentTag = dataset.getString("comp");
            if (solutionTag == null || solutionTag.isEmpty() || componentTag == null || componentTag.isEmpty()) {
                throw new IllegalStateException("Dataset lacks actual solution/component association");
            }
            ModelNode comp = model.component(componentTag);
            String[] geometryTags = comp.geom().tags();
            if (geometryTags.length != 1) throw new IllegalStateException("Sampling requires one unambiguous geometry in the dataset component");
            String geometryTag = geometryTags[0];
            GeomSequence geom = comp.geom(geometryTag);
            if (geom.getSDim() != 2 || !"m".equals(geom.lengthUnit())) throw new IllegalStateException("Sampling requires a 2D geometry in metres");
            double[] actualBounds = geom.getBoundingBox();
            if (actualBounds.length != 4) throw new IllegalStateException("Unexpected live geometry bounding box");
            for (int i = 0; i < 4; i++) if (Math.abs(actualBounds[i] - bounds[i]) > 1e-12) {
                throw new IllegalArgumentException("Requested full-domain bounds disagree with live geometry");
            }
            GeomFeature circle = null;
            for (String tag : geom.feature().tags()) {
                GeomFeature feature = geom.feature(tag);
                if ("Circle".equals(feature.getType())) {
                    if (circle != null) throw new IllegalStateException("Ambiguous cylinder geometry: multiple Circle features");
                    circle = feature;
                }
            }
            if (circle == null) throw new IllegalStateException("No Circle feature found for the cylinder mask");
            double[] center = circle.getDoubleArray("pos");
            double radius = circle.getDouble("r");
            if (center.length != 2 || !Double.isFinite(center[0]) || !Double.isFinite(center[1])
                    || !Double.isFinite(radius) || radius <= 0) throw new IllegalStateException("Invalid live circle geometry");
            int[][] boundaryClasses = classifyCylinderChannelBoundaries(geom, center[0], center[1], radius);

            SolverSequence solution = model.sol(solutionTag);
            String[] parameterNames = solution.getPNames();
            if (parameterNames.length != 1 || !"t".equals(parameterNames[0])) {
                throw new IllegalStateException("Saved solution must have only the transient parameter t");
            }
            double[] savedTimes = solution.getPVals();
            if (savedTimes == null || savedTimes.length == 0) throw new IllegalStateException("Saved solution has no time values");
            for (int i = 0; i < savedTimes.length; i++) {
                if (!Double.isFinite(savedTimes[i]) || (i > 0 && savedTimes[i] <= savedTimes[i - 1])) {
                    throw new IllegalStateException("Saved solution times must be finite and strictly increasing");
                }
            }
            String[] tokens = timesCsv.split(",");
            // Each invocation loads its own existing solution. Bound temporary Data
            // nodes to 32 while reusing the same verified per-frame export/check path.
            int minFrames = sequenceBatch ? 1 : 3, maxFrames = sequenceBatch ? 32 : 5;
            if (tokens.length < minFrames || tokens.length > maxFrames) {
                throw new IllegalArgumentException(sequenceBatch
                    ? "Sequence batch must contain 1 to 32 requested times"
                    : "Sample must contain 3 to 5 requested times");
            }
            double[] requested = new double[tokens.length], actualTimes = new double[tokens.length];
            int[] solutionNumbers = new int[tokens.length];
            for (int ti = 0; ti < tokens.length; ti++) {
                requested[ti] = Double.parseDouble(tokens[ti].trim());
                if (!Double.isFinite(requested[ti]) || (ti > 0 && requested[ti] <= requested[ti - 1])) {
                    throw new IllegalArgumentException("Requested times must be finite, unique and strictly increasing");
                }
                int match = -1;
                for (int si = 0; si < savedTimes.length; si++) {
                    if (Math.abs(savedTimes[si] - requested[ti]) <= 1e-12) {
                        if (match >= 0) throw new IllegalStateException("Multiple saved frames match requested time");
                        match = si;
                    }
                }
                if (match < 0) throw new IllegalArgumentException("Requested time " + requested[ti] + " is not an exact saved time; nearest-time substitution is forbidden");
                solutionNumbers[ti] = match + 1;
                actualTimes[ti] = savedTimes[match];
            }
            double[] gridx = fullFieldAxis(xmin, xmax, nx), gridy = fullFieldAxis(ymin, ymax, ny);
            String base = outputDir.replace('\\', '/');
            if (!base.endsWith("/")) base += "/";
            // The Python caller owns directory creation and no-overwrite admission.
            // Native ExportFeature performs the writes; java.io is not used.
            String[] expressions = new String[]{"u", "v", "p"};
            String[] directExpressions = new String[]{"u", "v", "p", "x", "y", "t"};
            DatasetFeature point = model.result().dataset().create("cpt_fullfield_check", "CutPoint2D");
            point.set("data", datasetTag);
            NumericalFeature evaluator = model.result().numerical().create("num_fullfield_check", "EvalPoint");
            evaluator.set("data", point.tag());
            evaluator.set("expr", directExpressions);
            evaluator.set("solrepresentation", "solnum");
            evaluator.set("innerinput", "manual");
            int[][] checkIndices = new int[][]{
                {(int)Math.round((nx - 1) * 0.125), (int)Math.round((ny - 1) * 0.5)},
                {(int)Math.round((nx - 1) * 0.375), (int)Math.round((ny - 1) * 0.5)},
                {(int)Math.round((nx - 1) * 0.75), (int)Math.round((ny - 1) * 0.75)}
            };
            java.util.Set<String> distinctChecks = new java.util.HashSet<String>();
            for (int[] ij : checkIndices) {
                if (!distinctChecks.add(ij[0] + "," + ij[1])) throw new IllegalArgumentException("Grid is too small for three distinct direct checks");
                if (Math.hypot(gridx[ij[0]] - center[0], gridy[ij[1]] - center[1]) <= radius + 1e-12) {
                    throw new IllegalArgumentException("A direct-check grid point is inside or on the cylinder");
                }
            }
            StringBuilder exports = new StringBuilder("["), direct = new StringBuilder("[");
            java.util.List<String> files = new java.util.ArrayList<String>();
            String[] actualUnits = null;
            int directCount = 0;
            for (int ti = 0; ti < requested.length; ti++) {
                String filename = "field_t" + ti + ".txt";
                ExportFeature exporter = model.result().export().create("exp_fullfield_" + ti, "Data");
                exporter.set("data", datasetTag);
                exporter.set("expr", expressions);
                exporter.set("solutionparams", "manual");
                exporter.set("innerinput", "manual");
                exporter.set("solnum", new int[]{solutionNumbers[ti]});
                exporter.set("filename", base + filename);
                exporter.set("location", "grid");
                exporter.set("gridstruct", "spreadsheet");
                exporter.set("gridx2", gridx);
                exporter.set("gridy2", gridy);
                exporter.set("header", "on");
                exporter.set("fullprec", "on");
                exporter.set("includecoords", true);
                exporter.set("includenan", true);
                // Units are never supplied: read what this executed export resolved.
                exporter.run();
                String[] readExpressions = exporter.getStringArray("expr");
                String[] readUnits = exporter.getStringArray("unit");
                int[] readSolnums = exporter.getIntArray("solnum");
                double[] readX = exporter.getDoubleArray("gridx2"), readY = exporter.getDoubleArray("gridy2");
                String readDataset = exporter.getString("data"), readInput = exporter.getString("innerinput");
                if (!Arrays.equals(readExpressions, expressions) || readUnits.length != 3
                        || readSolnums.length != 1 || readSolnums[0] != solutionNumbers[ti]
                        || !datasetTag.equals(readDataset) || !"manual".equals(readInput)
                        || !Arrays.equals(readX, gridx) || !Arrays.equals(readY, gridy)) {
                    throw new IllegalStateException("Executed Data export readback disagrees with requested sample");
                }
                for (String unit : readUnits) if (unit == null || unit.trim().isEmpty()) throw new IllegalStateException("Executed Data export has no resolved unit");
                if (actualUnits != null && !Arrays.equals(actualUnits, readUnits)) throw new IllegalStateException("Resolved units differ between saved frames");
                actualUnits = readUnits;
                files.add(filename);
                if (ti > 0) exports.append(",");
                exports.append("{\"filename\":").append(quote(filename))
                    .append(",\"export_tag\":").append(quote(exporter.tag()))
                    .append(",\"export_type\":").append(quote(exporter.getType()))
                    .append(",\"dataset_tag\":").append(quote(readDataset))
                    .append(",\"expressions\":").append(stringArrayToJson(readExpressions))
                    .append(",\"units\":").append(stringArrayToJson(readUnits))
                    .append(",\"innerinput\":").append(quote(readInput))
                    .append(",\"solnum\":").append(readSolnums[0])
                    .append(",\"target_t\":").append(requested[ti])
                    .append(",\"actual_t\":").append(savedTimes[readSolnums[0] - 1])
                    .append(",\"gridx\":").append(doubleArrayToJson(readX))
                    .append(",\"gridy\":").append(doubleArrayToJson(readY)).append("}");
                for (int[] ij : checkIndices) {
                    double x = gridx[ij[0]], y = gridy[ij[1]];
                    point.set("pointx", x);
                    point.set("pointy", y);
                    evaluator.set("solnum", new int[]{solutionNumbers[ti]});
                    double[] values = fullFieldSinglePoint(evaluator.getReal());
                    if (Math.abs(values[3] - x) > 1e-12 || Math.abs(values[4] - y) > 1e-12
                            || Math.abs(values[5] - requested[ti]) > 1e-12) {
                        throw new IllegalStateException("Independent EvalPoint returned a different actual coordinate/time");
                    }
                    if (directCount++ > 0) direct.append(",");
                    direct.append("{\"time_index\":").append(ti)
                        .append(",\"grid_ix\":").append(ij[0]).append(",\"grid_iy\":").append(ij[1])
                        .append(",\"target_t\":").append(requested[ti]).append(",\"actual_t\":").append(values[5])
                        .append(",\"requested_x\":").append(x).append(",\"requested_y\":").append(y)
                        .append(",\"x\":").append(values[3]).append(",\"y\":").append(values[4])
                        .append(",\"values\":").append(doubleArrayToJson(new double[]{values[0], values[1], values[2]}))
                        .append(",\"numerical_tag\":").append(quote(evaluator.tag()))
                        .append(",\"point_dataset_tag\":").append(quote(point.tag())).append("}");
                }
            }
            String[] actualDirectUnits = evaluator.getStringArray("unit");
            if (actualDirectUnits.length != directExpressions.length) throw new IllegalStateException("Missing independent expression units");
            for (String unit : actualDirectUnits) if (unit == null || unit.trim().isEmpty()) throw new IllegalStateException("Missing independent expression unit");
            exports.append("]"); direct.append("]");
            StringBuilder sb = new StringBuilder("{\n  \"status\":\"SUCCESS\",\n");
            sb.append("  \"operation\":\"export_full_field_sample\",\n")
              .append("  \"batch_mode\":").append(sequenceBatch).append(",\n")
              .append("  \"model_path\":").append(quote(modelPath)).append(",\n")
              .append("  \"model_tag\":").append(quote(model.tag())).append(",\n")
              .append("  \"comsol_version\":").append(quote(ModelUtil.getComsolVersion())).append(",\n")
              .append("  \"dataset\":{\"tag\":").append(quote(dataset.tag()))
              .append(",\"type\":").append(quote(dataset.getType()))
              .append(",\"solution_tag\":").append(quote(dataset.getString("solution"))).append("},\n")
              .append("  \"geometry\":{\"component_tag\":").append(quote(componentTag))
              .append(",\"geometry_tag\":").append(quote(geometryTag))
              .append(",\"association\":\"only geometry in actual dataset component\",\"length_unit\":").append(quote(geom.lengthUnit()))
              .append(",\"bounds\":").append(doubleArrayToJson(actualBounds))
              .append(",\"circle\":{\"feature_tag\":").append(quote(circle.tag()))
              .append(",\"center\":").append(doubleArrayToJson(center)).append(",\"radius\":").append(radius)
              .append(",\"unit\":").append(quote(geom.lengthUnit()))
              .append(",\"source\":\"live Circle feature pos/r and geometry lengthUnit\",\"boundary_ids\":")
              .append(intArrayToJson(boundaryClasses[3])).append("}},\n")
              .append("  \"saved_times\":").append(doubleArrayToJson(savedTimes)).append(",\n")
              .append("  \"requested_times\":").append(doubleArrayToJson(requested)).append(",\n")
              .append("  \"actual_times\":").append(doubleArrayToJson(actualTimes)).append(",\n")
              .append("  \"grid\":{\"nx\":").append(nx).append(",\"ny\":").append(ny)
              .append(",\"xmin\":").append(xmin).append(",\"xmax\":").append(xmax)
              .append(",\"ymin\":").append(ymin).append(",\"ymax\":").append(ymax).append("},\n")
              .append("  \"exports\":").append(exports).append(",\n")
              .append("  \"direct_evaluation_node\":{\"tag\":").append(quote(evaluator.tag()))
              .append(",\"type\":").append(quote(evaluator.getType()))
              .append(",\"dataset_tag\":").append(quote(evaluator.getString("data")))
              .append(",\"point_dataset_type\":").append(quote(point.getType()))
              .append(",\"source_dataset_tag\":").append(quote(point.getString("data")))
              .append(",\"expressions\":").append(stringArrayToJson(evaluator.getStringArray("expr")))
              .append(",\"units\":").append(stringArrayToJson(actualDirectUnits))
              .append(",\"solrepresentation\":").append(quote(evaluator.getString("solrepresentation")))
              .append(",\"innerinput\":").append(quote(evaluator.getString("innerinput"))).append("},\n")
              .append("  \"direct_evaluations\":").append(direct).append(",\n")
              .append("  \"exported_files\":").append(stringArrayToJson(files.toArray(new String[0]))).append("\n}");
            emitResult(sb.toString());
        } catch (Throwable t) {
            outputError("MODEL_VALIDATION_FAILED", "Full-field sampling failed: " + t.getMessage(), getStackTrace(t));
        }
    }

    private static double[] fullFieldAxis(double min, double max, int count) {
        double[] values = new double[count];
        for (int i = 0; i < count; i++) values[i] = min + (max - min) * i / (count - 1);
        values[0] = min;
        values[count - 1] = max;
        return values;
    }

    private static double[] fullFieldSinglePoint(double[][] matrix) {
        double[] values = new double[6];
        if (matrix.length == 6) {
            for (int i = 0; i < 6; i++) {
                if (matrix[i].length != 1) throw new IllegalStateException("EvalPoint must return one saved time at one point");
                values[i] = matrix[i][0];
            }
        } else if (matrix.length == 1 && matrix[0].length == 6) {
            values = matrix[0].clone();
        } else {
            throw new IllegalStateException("Unexpected independent EvalPoint result dimensions");
        }
        for (double value : values) if (!Double.isFinite(value)) throw new IllegalStateException("Non-finite independent EvalPoint value");
        return values;
    }

    private static void safeRemoveExport(Model model, String tag) {
        try {
            for (String t : model.result().export().tags()) {
                if (tag.equals(t)) {
                    model.result().export().remove(tag);
                    break;
                }
            }
        } catch (Throwable ignored) {}
    }

    private static void safeRemoveDataset(Model model, String tag) {
        try {
            for (String t : model.result().dataset().tags()) {
                if (tag.equals(t)) {
                    model.result().dataset().remove(tag);
                    break;
                }
            }
        } catch (Throwable ignored) {}
    }

    private static void safeRemoveResult(Model model, String tag) {
        try {
            for (String t : model.result().tags()) {
                if (tag.equals(t)) {
                    model.result().remove(tag);
                    break;
                }
            }
        } catch (Throwable ignored) {}
    }

    private static void safeRemoveNumerical(Model model, String tag) {
        try {
            for (String t : model.result().numerical().tags()) {
                if (tag.equals(t)) {
                    model.result().numerical().remove(tag);
                    break;
                }
            }
        } catch (Throwable ignored) {}
    }

    private static String safeGetSolverProperty(SolverFeature feat, String prop) {
        try {
            String v = feat.getString(prop);
            if (v != null) return v;
        } catch (Throwable ignored) {}
        try {
            double d = feat.getDouble(prop);
            return Double.toString(d);
        } catch (Throwable ignored) {}
        try {
            String[] arr = feat.getStringArray(prop);
            if (arr != null) return Arrays.toString(arr);
        } catch (Throwable ignored) {}
        return "UNOBTAINED";
    }

    private static void handleInspectProbeAndSolver(String modelPath, String probeX, String probeY, String probeOutputDir) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            outputError("COMSOL_CONNECTION_FAILED", "Failed to load model for probe and solver inspection: " + t.getMessage(), getStackTrace(t));
            return;
        }

        try {
            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"inspect_probe_and_solver\",\n");
            sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"probe_coords\": {\"x\": ").append(quote(probeX)).append(", \"y\": ").append(quote(probeY)).append("},\n");

            // Re-export from the loaded solution only. The source model is never saved.
            // Read the complete export -> plot -> point dataset -> solution chain AFTER run().
            sb.append("  \"probe_exports\": [\n");
            if (!probeOutputDir.isEmpty()) {
                DatasetFeature point = model.result().dataset().create("cpt_unit_evidence", "CutPoint2D");
                point.set("data", "dset1");
                point.set("pointx", probeX);
                point.set("pointy", probeY);
                String[] components = new String[]{"u", "v"};
                for (int i = 0; i < components.length; i++) {
                    String component = components[i];
                    ResultFeature pg = model.result().create("pg_unit_" + component, "PlotGroup1D");
                    pg.set("data", point.tag());
                    ResultFeature feature = pg.feature().create("point", "PointGraph");
                    feature.set("expr", component);
                    // Do not set unit: COMSOL resolves it from this expression and dataset.
                    ExportFeature exporter = model.result().export().create("exp_unit_" + component, "Plot");
                    exporter.set("plotgroup", pg.tag());
                    String baseProbeDir = probeOutputDir.replace('\\', '/');
                    if (!baseProbeDir.endsWith("/")) {
                        baseProbeDir = baseProbeDir + "/";
                    }
                    exporter.set("filename", baseProbeDir + "wake_" + component + ".txt");
                    exporter.run();

                    ResultFeature actualPg = model.result(exporter.getString("plotgroup"));
                    String[] actualFeatures = actualPg.feature().tags();
                    if (actualFeatures.length != 1) throw new IllegalStateException("Probe plot must contain exactly one feature");
                    ResultFeature actualFeature = actualPg.feature(actualFeatures[0]);
                    String featureData = actualFeature.getString("data");
                    if (!"parent".equals(featureData) && !actualPg.getString("data").equals(featureData)) {
                        throw new IllegalStateException("PointGraph overrides the probe dataset: " + featureData);
                    }
                    DatasetFeature actualPoint = model.result().dataset(actualPg.getString("data"));
                    DatasetFeature actualSolution = model.result().dataset(actualPoint.getString("data"));
                    sb.append("    {\n");
                    sb.append("      \"component\": ").append(quote(component)).append(",\n");
                    sb.append("      \"model_path\": ").append(quote(modelPath)).append(",\n");
                    sb.append("      \"model_tag\": ").append(quote(model.tag())).append(",\n");
                    sb.append("      \"export_tag\": ").append(quote(exporter.tag())).append(",\n");
                    sb.append("      \"filename\": ").append(quote(exporter.getString("filename"))).append(",\n");
                    sb.append("      \"plotgroup_tag\": ").append(quote(actualPg.tag())).append(",\n");
                    sb.append("      \"feature_tags\": ").append(stringArrayToJson(actualFeatures)).append(",\n");
                    sb.append("      \"feature_tag\": ").append(quote(actualFeature.tag())).append(",\n");
                    sb.append("      \"expression\": ").append(quote(actualFeature.getString("expr"))).append(",\n");
                    sb.append("      \"unit\": ").append(quote(actualFeature.getString("unit"))).append(",\n");
                    sb.append("      \"feature_data\": ").append(quote(featureData)).append(",\n");
                    sb.append("      \"dataset_tag\": ").append(quote(actualPoint.tag())).append(",\n");
                    sb.append("      \"dataset_type\": ").append(quote(actualPoint.getType())).append(",\n");
                    sb.append("      \"point_x\": ").append(quote(actualPoint.getString("pointx"))).append(",\n");
                    sb.append("      \"point_y\": ").append(quote(actualPoint.getString("pointy"))).append(",\n");
                    sb.append("      \"solution_dataset\": ").append(quote(actualSolution.tag())).append(",\n");
                    sb.append("      \"solution_type\": ").append(quote(actualSolution.getType())).append(",\n");
                    sb.append("      \"solution_tag\": ").append(quote(actualSolution.getString("solution"))).append("\n");
                    sb.append("    }").append(i < components.length - 1 ? "," : "").append("\n");
                }
            }
            sb.append("  ],\n");

            // 1. Datasets inspection
            sb.append("  \"datasets\": [\n");
            String[] dtags = model.result().dataset().tags();
            for (int i = 0; i < dtags.length; i++) {
                String dtag = dtags[i];
                DatasetFeature dset = model.result().dataset(dtag);
                String dtype = dset.getType();
                String dataSrc = "";
                try { dataSrc = dset.getString("data"); } catch (Throwable ignored) {}
                String px = "", py = "";
                try { px = dset.getString("pointx"); } catch (Throwable ignored) {}
                try { py = dset.getString("pointy"); } catch (Throwable ignored) {}

                sb.append("    {\n");
                sb.append("      \"tag\": ").append(quote(dtag)).append(",\n");
                sb.append("      \"type\": ").append(quote(dtype)).append(",\n");
                sb.append("      \"data_source\": ").append(quote(dataSrc)).append(",\n");
                sb.append("      \"pointx\": ").append(quote(px)).append(",\n");
                sb.append("      \"pointy\": ").append(quote(py)).append("\n");
                sb.append("    }").append(i < dtags.length - 1 ? "," : "").append("\n");
            }
            sb.append("  ],\n");

            // 2. Plotgroups inspection
            sb.append("  \"plotgroups\": [\n");
            String[] pgtags = model.result().tags();
            java.util.List<String> plotGroupList = new java.util.ArrayList<>();
            for (String pt : pgtags) {
                if (model.result(pt).getType().contains("PlotGroup")) {
                    plotGroupList.add(pt);
                }
            }
            for (int i = 0; i < plotGroupList.size(); i++) {
                String pgtag = plotGroupList.get(i);
                ResultFeature rfeat = model.result(pgtag);
                String rtype = rfeat.getType();
                String dset = "";
                try { dset = rfeat.getString("data"); } catch (Throwable ignored) {}

                sb.append("    {\n");
                sb.append("      \"tag\": ").append(quote(pgtag)).append(",\n");
                sb.append("      \"type\": ").append(quote(rtype)).append(",\n");
                sb.append("      \"data\": ").append(quote(dset)).append(",\n");
                sb.append("      \"features\": [\n");
                String[] ftags = rfeat.feature().tags();
                for (int j = 0; j < ftags.length; j++) {
                    String ftag = ftags[j];
                    ResultFeature feat = rfeat.feature(ftag);
                    String expr = "", unit = "", descr = "";
                    try { expr = feat.getString("expr"); } catch (Throwable ignored) {}
                    try { unit = feat.getString("unit"); } catch (Throwable ignored) {}
                    try { descr = feat.getString("descr"); } catch (Throwable ignored) {}
                    sb.append("        {\n");
                    sb.append("          \"tag\": ").append(quote(ftag)).append(",\n");
                    sb.append("          \"type\": ").append(quote(feat.getType())).append(",\n");
                    sb.append("          \"expr\": ").append(quote(expr)).append(",\n");
                    sb.append("          \"unit\": ").append(quote(unit)).append(",\n");
                    sb.append("          \"descr\": ").append(quote(descr)).append("\n");
                    sb.append("        }").append(j < ftags.length - 1 ? "," : "").append("\n");
                }
                sb.append("      ]\n");
                sb.append("    }").append(i < plotGroupList.size() - 1 ? "," : "").append("\n");
            }
            sb.append("  ],\n");

            // 3. Direct independent COMSOL point numerical evaluation
            sb.append("  \"direct_evaluations\": [\n");
            safeRemoveDataset(model, "cpt_s1b1_eval");
            DatasetFeature evalCpt = model.result().dataset().create("cpt_s1b1_eval", "CutPoint2D");
            evalCpt.set("data", "dset1");
            evalCpt.set("pointx", probeX);
            evalCpt.set("pointy", probeY);

            safeRemoveNumerical(model, "num_s1b1_eval");
            NumericalFeature numEval = model.result().numerical().create("num_s1b1_eval", "EvalPoint");
            numEval.set("data", "cpt_s1b1_eval");
            numEval.set("expr", new String[]{"u", "v"});

            double[] pvals = model.sol("sol1").getPVals();
            double[][] realMatrix = null;
            try {
                try {
                    numEval.getClass().getMethod("setResult").invoke(numEval);
                } catch (Throwable ignored) {}
                realMatrix = numEval.getReal();
            } catch (Throwable t) {
                try {
                    Object dataObj = numEval.getClass().getMethod("getData").invoke(numEval);
                    if (dataObj instanceof double[][]) {
                        realMatrix = (double[][]) dataObj;
                    }
                } catch (Throwable ignored) {}
            }

            double[] targetTimes = new double[]{180.0, 185.0, 190.0, 195.0, 200.0};
            for (int tIdx = 0; tIdx < targetTimes.length; tIdx++) {
                double tt = targetTimes[tIdx];
                int bestIdx = -1;
                double bestDiff = 1e9;
                if (pvals != null) {
                    for (int p = 0; p < pvals.length; p++) {
                        double diff = Math.abs(pvals[p] - tt);
                        if (diff < bestDiff) {
                            bestDiff = diff;
                            bestIdx = p;
                        }
                    }
                }

                double evalU = Double.NaN;
                double evalV = Double.NaN;
                double actualT = (bestIdx >= 0) ? pvals[bestIdx] : Double.NaN;

                if (realMatrix != null && bestIdx >= 0) {
                    if (realMatrix.length == pvals.length && realMatrix[0].length >= 2) {
                        evalU = realMatrix[bestIdx][0];
                        evalV = realMatrix[bestIdx][1];
                    } else if (realMatrix.length >= 2 && realMatrix[0].length == pvals.length) {
                        evalU = realMatrix[0][bestIdx];
                        evalV = realMatrix[1][bestIdx];
                    }
                }

                sb.append("    {\n");
                sb.append("      \"target_t\": ").append(tt).append(",\n");
                sb.append("      \"actual_t\": ").append(Double.isFinite(actualT) ? Double.toString(actualT) : "null").append(",\n");
                sb.append("      \"point_x\": ").append(quote(probeX)).append(",\n");
                sb.append("      \"point_y\": ").append(quote(probeY)).append(",\n");
                sb.append("      \"u_direct\": ").append(Double.isNaN(evalU) ? "null" : Double.toString(evalU)).append(",\n");
                sb.append("      \"v_direct\": ").append(Double.isNaN(evalV) ? "null" : Double.toString(evalV)).append("\n");
                sb.append("    }").append(tIdx < targetTimes.length - 1 ? "," : "").append("\n");
            }
            sb.append("  ],\n");

            // 4. Mesh statistics & properties
            sb.append("  \"mesh_details\": {\n");
            MeshSequence mesh = model.component("comp1").mesh("mesh1");
            sb.append("    \"num_elem\": ").append(mesh.getNumElem()).append(",\n");
            sb.append("    \"num_vertex\": ").append(mesh.getNumVertex()).append(",\n");
            sb.append("    \"sdim\": ").append(mesh.getSDim()).append(",\n");
            sb.append("    \"is_complete\": ").append(mesh.isComplete()).append(",\n");
            sb.append("    \"features\": ").append(stringArrayToJson(mesh.feature().tags())).append("\n");
            sb.append("  },\n");

            // 5. Physics discretization & initial conditions
            sb.append("  \"physics_details\": {\n");
            String shapeOrder = "UNOBTAINED";
            String initU = "UNOBTAINED", initV = "UNOBTAINED", initP = "UNOBTAINED";
            try {
                Object spf = model.component("comp1").physics("spf");
                try {
                    Object shapeProp = spf.getClass().getMethod("prop", String.class).invoke(spf, "ShapeProperty");
                    shapeOrder = (String) shapeProp.getClass().getMethod("getString", String.class).invoke(shapeProp, "order");
                } catch (Throwable ignored) {}

                try {
                    Object initFeat = spf.getClass().getMethod("feature", String.class).invoke(spf, "init1");
                    initU = (String) initFeat.getClass().getMethod("getString", String.class).invoke(initFeat, "u");
                    initV = (String) initFeat.getClass().getMethod("getString", String.class).invoke(initFeat, "v");
                    initP = (String) initFeat.getClass().getMethod("getString", String.class).invoke(initFeat, "p");
                } catch (Throwable ignored) {}
            } catch (Throwable ignored) {}

            sb.append("    \"shape_order\": ").append(quote(shapeOrder)).append(",\n");
            sb.append("    \"initial_conditions\": {\n");
            sb.append("      \"u\": ").append(quote(initU)).append(",\n");
            sb.append("      \"v\": ").append(quote(initV)).append(",\n");
            sb.append("      \"p\": ").append(quote(initP)).append("\n");
            sb.append("    }\n");
            sb.append("  },\n");

            // 6. Detailed live solver configuration
            sb.append("  \"solver_details\": {\n");
            SolverSequence sol = model.sol("sol1");
            for (String fTag : sol.feature().tags()) {
                SolverFeature feat = sol.feature(fTag);
                if ("Time".equalsIgnoreCase(feat.getType())) {
                    sb.append("    \"timemethod\": ").append(quote(safeGetSolverProperty(feat, "timemethod"))).append(",\n");
                    sb.append("    \"tstepsbdf\": ").append(quote(safeGetSolverProperty(feat, "tstepsbdf"))).append(",\n");
                    sb.append("    \"maxorder\": ").append(quote(safeGetSolverProperty(feat, "maxorder"))).append(",\n");
                    sb.append("    \"minorder\": ").append(quote(safeGetSolverProperty(feat, "minorder"))).append(",\n");
                    sb.append("    \"rtol\": ").append(quote(safeGetSolverProperty(feat, "rtol"))).append(",\n");
                    sb.append("    \"atolmethod\": ").append(quote(safeGetSolverProperty(feat, "atolmethod"))).append(",\n");
                    sb.append("    \"atolfactor\": ").append(quote(safeGetSolverProperty(feat, "atolfactor"))).append(",\n");
                    sb.append("    \"atol\": ").append(quote(safeGetSolverProperty(feat, "atol"))).append(",\n");
                    sb.append("    \"atolvaluemethod\": ").append(quote(safeGetSolverProperty(feat, "atolvaluemethod"))).append(",\n");
                    sb.append("    \"atolglobal\": ").append(quote(safeGetSolverProperty(feat, "atolglobal"))).append(",\n");
                    sb.append("    \"atolglobalfactor\": ").append(quote(safeGetSolverProperty(feat, "atolglobalfactor"))).append(",\n");
                    sb.append("    \"atolglobalmethod\": ").append(quote(safeGetSolverProperty(feat, "atolglobalmethod"))).append(",\n");
                    sb.append("    \"atolglobalvaluemethod\": ").append(quote(safeGetSolverProperty(feat, "atolglobalvaluemethod"))).append(",\n");
                    sb.append("    \"maxstepconstraintbdf\": ").append(quote(safeGetSolverProperty(feat, "maxstepconstraintbdf"))).append(",\n");
                    sb.append("    \"maxstepbdf\": ").append(quote(safeGetSolverProperty(feat, "maxstepbdf"))).append(",\n");
                    sb.append("    \"initialstepbdf\": ").append(quote(safeGetSolverProperty(feat, "initialstepbdf"))).append(",\n");
                    sb.append("    \"initialstepbdfactive\": ").append(quote(safeGetSolverProperty(feat, "initialstepbdfactive"))).append(",\n");
                    sb.append("    \"tlist\": ").append(quote(safeGetSolverProperty(feat, "tlist"))).append("\n");
                    break;
                }
            }
            sb.append("  }\n");
            sb.append("}\n");

            emitResult(sb.toString());

        } catch (Throwable t) {
            outputError("SOLVER_FAILED", "Failed to inspect probe and solver: " + t.getMessage(), getStackTrace(t));
        }
    }

    private static void handleExportS1Diagnostics(String modelPath, String outputDir, String snapshotTimesStr) {
        Model model;
        try {
            model = ModelUtil.loadCopy("Model", modelPath);
        } catch (Throwable t) {
            outputError("COMSOL_CONNECTION_FAILED", "Failed to load model for diagnostic export: " + t.getMessage(), getStackTrace(t));
            return;
        }

        try {
            String base = outputDir.replace('\\', '/');
            if (!base.endsWith("/")) {
                base = base + "/";
            }

            java.util.List<String> exportedFiles = new java.util.ArrayList<>();

            // 1. Lift coefficient (pg3)
            String liftPath = base + "lift_coeff.txt";
            safeRemoveExport(model, "exp_lift_s1");
            ExportFeature expLift = model.result().export().create("exp_lift_s1", "Plot");
            expLift.set("plotgroup", "pg3");
            expLift.set("filename", liftPath);
            expLift.run();
            exportedFiles.add("lift_coeff.txt");

            // 2. Drag coefficient (pg4)
            String dragPath = base + "drag_coeff.txt";
            safeRemoveExport(model, "exp_drag_s1");
            ExportFeature expDrag = model.result().export().create("exp_drag_s1", "Plot");
            expDrag.set("plotgroup", "pg4");
            expDrag.set("filename", dragPath);
            expDrag.run();
            exportedFiles.add("drag_coeff.txt");

            // 3. Wake probe at CutPoint2D (0.15, 0.12)
            safeRemoveDataset(model, "cpt_wake_s1");
            DatasetFeature cptWake = model.result().dataset().create("cpt_wake_s1", "CutPoint2D");
            cptWake.set("data", "dset1");
            cptWake.set("pointx", "0.15");
            cptWake.set("pointy", "0.12");

            // Wake U
            safeRemoveResult(model, "pg_wake_u_s1");
            ResultFeature pgWakeU = model.result().create("pg_wake_u_s1", "PlotGroup1D");
            pgWakeU.set("data", "cpt_wake_s1");
            ResultFeature ptWakeU = pgWakeU.feature().create("ptU", "PointGraph");
            ptWakeU.set("expr", "u");
            String wuPath = base + "wake_u.txt";
            safeRemoveExport(model, "exp_wake_u_s1");
            ExportFeature expWakeU = model.result().export().create("exp_wake_u_s1", "Plot");
            expWakeU.set("plotgroup", "pg_wake_u_s1");
            expWakeU.set("filename", wuPath);
            expWakeU.run();
            exportedFiles.add("wake_u.txt");

            // Wake V
            safeRemoveResult(model, "pg_wake_v_s1");
            ResultFeature pgWakeV = model.result().create("pg_wake_v_s1", "PlotGroup1D");
            pgWakeV.set("data", "cpt_wake_s1");
            ResultFeature ptWakeV = pgWakeV.feature().create("ptV", "PointGraph");
            ptWakeV.set("expr", "v");
            String wvPath = base + "wake_v.txt";
            safeRemoveExport(model, "exp_wake_v_s1");
            ExportFeature expWakeV = model.result().export().create("exp_wake_v_s1", "Plot");
            expWakeV.set("plotgroup", "pg_wake_v_s1");
            expWakeV.set("filename", wvPath);
            expWakeV.run();
            exportedFiles.add("wake_v.txt");

            // 4. Inlet probe at CutPoint2D (0.0, 0.12)
            safeRemoveDataset(model, "cpt_inlet_s1");
            DatasetFeature cptInlet = model.result().dataset().create("cpt_inlet_s1", "CutPoint2D");
            cptInlet.set("data", "dset1");
            cptInlet.set("pointx", "0.0");
            cptInlet.set("pointy", "0.12");

            // Inlet U
            safeRemoveResult(model, "pg_inlet_u_s1");
            ResultFeature pgInletU = model.result().create("pg_inlet_u_s1", "PlotGroup1D");
            pgInletU.set("data", "cpt_inlet_s1");
            ResultFeature ptInletU = pgInletU.feature().create("ptU", "PointGraph");
            ptInletU.set("expr", "u");
            String iuPath = base + "inlet_u.txt";
            safeRemoveExport(model, "exp_inlet_u_s1");
            ExportFeature expInletU = model.result().export().create("exp_inlet_u_s1", "Plot");
            expInletU.set("plotgroup", "pg_inlet_u_s1");
            expInletU.set("filename", iuPath);
            expInletU.run();
            exportedFiles.add("inlet_u.txt");

            // Inlet P
            safeRemoveResult(model, "pg_inlet_p_s1");
            ResultFeature pgInletP = model.result().create("pg_inlet_p_s1", "PlotGroup1D");
            pgInletP.set("data", "cpt_inlet_s1");
            ResultFeature ptInletP = pgInletP.feature().create("ptP", "PointGraph");
            ptInletP.set("expr", "p");
            String ipPath = base + "inlet_p.txt";
            safeRemoveExport(model, "exp_inlet_p_s1");
            ExportFeature expInletP = model.result().export().create("exp_inlet_p_s1", "Plot");
            expInletP.set("plotgroup", "pg_inlet_p_s1");
            expInletP.set("filename", ipPath);
            expInletP.run();
            exportedFiles.add("inlet_p.txt");

            // 5. Stagnation Point probe at CutPoint2D (0.095, 0.12)
            safeRemoveDataset(model, "cpt_stag_s1");
            DatasetFeature cptStag = model.result().dataset().create("cpt_stag_s1", "CutPoint2D");
            cptStag.set("data", "dset1");
            cptStag.set("pointx", "0.095");
            cptStag.set("pointy", "0.12");

            safeRemoveResult(model, "pg_stag_p_s1");
            ResultFeature pgStagP = model.result().create("pg_stag_p_s1", "PlotGroup1D");
            pgStagP.set("data", "cpt_stag_s1");
            ResultFeature ptStagP = pgStagP.feature().create("ptP", "PointGraph");
            ptStagP.set("expr", "p");
            String spPath = base + "stag_p.txt";
            safeRemoveExport(model, "exp_stag_p_s1");
            ExportFeature expStagP = model.result().export().create("exp_stag_p_s1", "Plot");
            expStagP.set("plotgroup", "pg_stag_p_s1");
            expStagP.set("filename", spPath);
            expStagP.run();
            exportedFiles.add("stag_p.txt");

            // 6. Vorticity snapshot images (dynamic solnum & arbitrary snapshot time schedules)
            double[] pvals = null;
            try {
                pvals = model.sol("sol1").getPVals();
            } catch (Throwable ignored) {}

            double tMax = (pvals != null && pvals.length > 0) ? pvals[pvals.length - 1] : 100.0;
            double[] targetTimes;
            if (snapshotTimesStr != null && !snapshotTimesStr.trim().isEmpty()) {
                String[] parts = snapshotTimesStr.split(",");
                targetTimes = new double[parts.length];
                for (int i = 0; i < parts.length; i++) {
                    targetTimes[i] = Double.parseDouble(parts[i].trim());
                }
            } else if (tMax > 105.0) {
                targetTimes = new double[]{10.0, 40.0, 100.0, 195.0, 200.0};
            } else {
                targetTimes = new double[]{10.0, 40.0, 95.0, 100.0};
            }

            // Configure dedicated zoom view for wake visualization (20D downstream, 10D lateral)
            View viewZoom = null;
            try {
                viewZoom = (View) model.view("view_wake_zoom_s1");
            } catch (Throwable t) {
                viewZoom = (View) model.view().create("view_wake_zoom_s1", 2);
            }
            ViewFeature axisZoom = viewZoom.axis();
            axisZoom.set("xmin", 0.08);
            axisZoom.set("xmax", 0.30);
            axisZoom.set("ymin", 0.07);
            axisZoom.set("ymax", 0.17);

            for (double tTarget : targetTimes) {
                int solnum = 1;
                if (pvals != null && pvals.length > 0) {
                    int bestIdx = 0;
                    double bestDiff = Math.abs(pvals[0] - tTarget);
                    for (int i = 1; i < pvals.length; i++) {
                        double diff = Math.abs(pvals[i] - tTarget);
                        if (diff < bestDiff) {
                            bestDiff = diff;
                            bestIdx = i;
                        }
                    }
                    solnum = bestIdx + 1; // 1-based in COMSOL
                }
                int tSec = (int) Math.round(tTarget);
                try {
                    String pgTag = "pg_vort_snap_" + tSec + "_s1";
                    safeRemoveResult(model, pgTag);
                    ResultFeature pgVort = model.result().create(pgTag, "PlotGroup2D");
                    pgVort.set("data", "dset1");
                    pgVort.set("solnum", String.valueOf(solnum));
                    pgVort.set("view", "view_wake_zoom_s1");
                    pgVort.set("titletype", "custom");
                    pgVort.set("title", "Re=100 Vorticity \\omega_z at t = " + tSec + " s (Zoom: x \\in [0.08, 0.30] m, y \\in [0.07, 0.17] m)");
                    pgVort.set("showlegends", "on");
                    pgVort.set("showlegendstitle", "on");
                    pgVort.set("showlegendsunit", "on");
                    pgVort.set("legendpos", "right");

                    ResultFeature surf = pgVort.feature().create("surf1", "Surface");
                    surf.set("expr", "vx-uy");
                    surf.set("unit", "1/s");
                    surf.set("colortable", "Wave"); // Red-White-Blue diverging colormap
                    surf.set("rangecoloractive", true);
                    double omegaLim = 2.0; // Symmetrical zero-centered limits [-2.0, +2.0] s^-1
                    surf.set("rangecolormin", -omegaLim);
                    surf.set("rangecolormax", omegaLim);
                    surf.set("colorlegend", true);
                    surf.set("showlegendtitle", true);
                    surf.set("legendtitle", "Vorticity \\omega_z [s^{-1}]");

                    String imgName = "vorticity_t" + tSec + "s_comsol.png";
                    String imgPath = base + imgName;
                    String expTag = "exp_img_" + tSec + "_s1";
                    safeRemoveExport(model, expTag);
                    ExportFeature expImg = model.result().export().create(expTag, "Image");
                    expImg.set("plotgroup", pgTag);
                    expImg.set("pngfilename", imgPath);
                    expImg.set("size", "manualweb");
                    expImg.set("unit", "px");
                    expImg.set("webwidth", "1400");
                    expImg.set("webheight", "650");
                    expImg.set("options2d", "on");
                    expImg.set("title2d", "on");
                    expImg.set("legend2d", "on");
                    expImg.set("axes2d", "on");
                    expImg.set("logo2d", "off");
                    expImg.set("fontsize", 10);
                    expImg.run();
                    exportedFiles.add(imgName);

                    // Export regular 2D grid data of exact vorticity (vx-uy) in the wake region
                    try {
                        String gridName = "vorticity_grid_t" + tSec + "s.txt";
                        String gridPath = base + gridName;
                        String expGridTag = "exp_grid_" + tSec + "_s1";
                        safeRemoveExport(model, expGridTag);
                        ExportFeature expGrid = model.result().export().create(expGridTag, "Data");
                        expGrid.set("data", "dset1");
                        expGrid.set("solnum", String.valueOf(solnum));
                        expGrid.set("expr", new String[]{"vx-uy"});
                        expGrid.set("unit", new String[]{"1/s"});
                        expGrid.set("descr", new String[]{"vorticity"});
                        expGrid.set("filename", gridPath);
                        expGrid.set("location", "grid");
                        expGrid.set("gridstruct", "spreadsheet");
                        expGrid.set("gridx2", "range(0.08, 0.0005, 0.30)");
                        expGrid.set("gridy2", "range(0.07, 0.0005, 0.17)");
                        expGrid.run();
                        exportedFiles.add(gridName);
                    } catch (Throwable gridErr) {
                        // Non-fatal fallback: continue if grid export unsupported
                    }

                    // Export full domain reference overview at final checkpoint
                    if (tSec == 200 || (tSec == 100 && tMax <= 105.0)) {
                        try {
                            String pgOvTag = "pg_vort_overview_" + tSec + "_s1";
                            safeRemoveResult(model, pgOvTag);
                            ResultFeature pgOv = model.result().create(pgOvTag, "PlotGroup2D");
                            pgOv.set("data", "dset1");
                            pgOv.set("solnum", String.valueOf(solnum));
                            pgOv.set("titletype", "custom");
                            pgOv.set("title", "Re=100 Vorticity \\omega_z (Full Domain 40D x 24D) at t = " + tSec + " s");

                            ResultFeature surfOv = pgOv.feature().create("surf_ov", "Surface");
                            surfOv.set("expr", "vx-uy");
                            surfOv.set("unit", "1/s");
                            surfOv.set("colortable", "Wave");
                            surfOv.set("rangecoloractive", true);
                            surfOv.set("rangecolormin", -omegaLim);
                            surfOv.set("rangecolormax", omegaLim);
                            surfOv.set("colorlegend", true);
                            surfOv.set("showlegendtitle", true);
                            surfOv.set("legendtitle", "Vorticity \\omega_z [s^{-1}]");

                            String ovImgName = "vorticity_overview_t" + tSec + "s_comsol.png";
                            String ovImgPath = base + ovImgName;
                            String expOvTag = "exp_img_ov_" + tSec + "_s1";
                            safeRemoveExport(model, expOvTag);
                            ExportFeature expOv = model.result().export().create(expOvTag, "Image");
                            expOv.set("plotgroup", pgOvTag);
                            expOv.set("pngfilename", ovImgPath);
                            expOv.set("size", "manualweb");
                            expOv.set("unit", "px");
                            expOv.set("webwidth", "1600");
                            expOv.set("webheight", "800");
                            expOv.set("options2d", "on");
                            expOv.set("title2d", "on");
                            expOv.set("legend2d", "on");
                            expOv.set("axes2d", "on");
                            expOv.set("logo2d", "off");
                            expOv.set("fontsize", 10);
                            expOv.run();
                            exportedFiles.add(ovImgName);
                        } catch (Throwable ovErr) {
                            // Non-fatal
                        }
                    }
                } catch (Throwable imgErr) {
                    outputError("SOLVER_FAILED", "Failed to export snapshot at t=" + tSec + "s: " + imgErr.getMessage(), getStackTrace(imgErr));
                    return;
                }
            }

            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"status\": \"SUCCESS\",\n");
            sb.append("  \"operation\": \"export_s1_diagnostics\",\n");
            sb.append("  \"model_path\": ").append(quote(modelPath)).append(",\n");
            sb.append("  \"output_dir\": ").append(quote(outputDir)).append(",\n");
            sb.append("  \"exported_files\": ").append(stringArrayToJson(exportedFiles.toArray(new String[0]))).append("\n");
            sb.append("}\n");
            emitResult(sb.toString());
        } catch (Throwable t) {
            outputError("SOLVER_FAILED", "Failed to export S1 diagnostics: " + t.getMessage(), getStackTrace(t));
        }
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

    private static final int BOUNDARY_SAMPLES = 17;

    /** Sample one 2D boundary edge (1-based id) at BOUNDARY_SAMPLES parameter values via GeomSequence.edgeX. */
    private static double[][] sampleEdge(GeomSequence geom, int b) {
        double[] pr = geom.edgeParamRange(b);
        double[] s = new double[BOUNDARY_SAMPLES];
        for (int i = 0; i < BOUNDARY_SAMPLES; i++) {
            s[i] = pr[0] + (pr[1] - pr[0]) * i / (BOUNDARY_SAMPLES - 1);
        }
        double[][] x = geom.edgeX(b, s);
        if (x == null || x.length != BOUNDARY_SAMPLES || x[0].length != 2) {
            throw new IllegalStateException("edgeX returned unexpected shape for boundary " + b);
        }
        return x;
    }

    /**
     * Live boundary geometry evidence queried from the COMSOL geometry kernel
     * (getNBoundaries, getBoundingBox, getUpDown, edgeParamRange, edgeX).
     * Nothing here is assumed: if the query fails, "available": false is reported.
     */
    private static String boundaryGeometryJson(GeomSequence geom, String gtag, String ind) {
        StringBuilder sb = new StringBuilder();
        try {
            int sdim = geom.getSDim();
            if (sdim != 2) {
                return "{\"available\": false, \"geometry_tag\": " + quote(gtag) + ", \"sdim\": " + sdim
                    + ", \"reason\": \"edge sampling implemented for 2D geometries only\"}";
            }
            boolean rebuilt = false;
            int nb = geom.getNBoundaries();
            if (nb == 0) {
                geom.run();
                rebuilt = true;
                nb = geom.getNBoundaries();
            }
            double[] bb = geom.getBoundingBox();
            int[][] ud = geom.getUpDown();
            sb.append("{\n");
            sb.append(ind).append("  \"available\": true,\n");
            sb.append(ind).append("  \"source\": \"COMSOL GeomSequence live query (getNBoundaries, getBoundingBox, getUpDown, edgeParamRange, edgeX)\",\n");
            sb.append(ind).append("  \"geometry_tag\": ").append(quote(gtag)).append(",\n");
            sb.append(ind).append("  \"geometry_rebuilt_in_memory\": ").append(rebuilt).append(",\n");
            sb.append(ind).append("  \"n_boundaries\": ").append(nb).append(",\n");
            sb.append(ind).append("  \"n_domains\": ").append(geom.getNDomains()).append(",\n");
            sb.append(ind).append("  \"bounding_box\": ").append(doubleArrayToJson(bb)).append(",\n");
            sb.append(ind).append("  \"samples_per_boundary\": ").append(BOUNDARY_SAMPLES).append(",\n");
            sb.append(ind).append("  \"boundaries\": [\n");
            for (int b = 1; b <= nb; b++) {
                double[][] x = sampleEdge(geom, b);
                double xmin = Double.POSITIVE_INFINITY, xmax = Double.NEGATIVE_INFINITY;
                double ymin = Double.POSITIVE_INFINITY, ymax = Double.NEGATIVE_INFINITY;
                double len = 0.0;
                StringBuilder pts = new StringBuilder("[");
                for (int i = 0; i < x.length; i++) {
                    xmin = Math.min(xmin, x[i][0]); xmax = Math.max(xmax, x[i][0]);
                    ymin = Math.min(ymin, x[i][1]); ymax = Math.max(ymax, x[i][1]);
                    if (i > 0) len += Math.hypot(x[i][0] - x[i - 1][0], x[i][1] - x[i - 1][1]);
                    pts.append("[").append(x[i][0]).append(", ").append(x[i][1]).append("]");
                    if (i < x.length - 1) pts.append(", ");
                }
                pts.append("]");
                sb.append(ind).append("    {\"id\": ").append(b)
                  .append(", \"x_range\": [").append(xmin).append(", ").append(xmax).append("]")
                  .append(", \"y_range\": [").append(ymin).append(", ").append(ymax).append("]")
                  .append(", \"length_polyline\": ").append(len)
                  .append(", \"up_domain\": ").append(ud[0][b - 1])
                  .append(", \"down_domain\": ").append(ud[1][b - 1])
                  .append(", \"samples\": ").append(pts)
                  .append("}").append(b < nb ? "," : "").append("\n");
            }
            sb.append(ind).append("  ]\n");
            sb.append(ind).append("}");
            return sb.toString();
        } catch (Throwable t) {
            return "{\"available\": false, \"geometry_tag\": " + quote(gtag)
                + ", \"error\": " + quote(t.getClass().getSimpleName() + ": " + t.getMessage()) + "}";
        }
    }

    /**
     * Classify every boundary of a rectangular channel with an embedded circle using
     * live edge samples. Returns {inlet(x=xmin), outlet(x=xmax), lateral(y=ymin|ymax), cylinder(|r-rc|=R)}.
     * Fails closed if any boundary is unclassified/ambiguous or any class is empty.
     */
    private static int[][] classifyCylinderChannelBoundaries(GeomSequence geom, double xc, double yc, double r) {
        if (geom.getSDim() != 2) throw new IllegalStateException("Boundary classification requires a 2D geometry");
        int nb = geom.getNBoundaries();
        double[] bb = geom.getBoundingBox();
        double tol = 1e-6 * Math.max(bb[1] - bb[0], bb[3] - bb[2]);
        java.util.List<Integer> inlet = new java.util.ArrayList<>(), outlet = new java.util.ArrayList<>();
        java.util.List<Integer> lateral = new java.util.ArrayList<>(), cyl = new java.util.ArrayList<>();
        boolean hasBottom = false, hasTop = false;
        for (int b = 1; b <= nb; b++) {
            double[][] x = sampleEdge(geom, b);
            boolean atXmin = true, atXmax = true, atYmin = true, atYmax = true, onCircle = true;
            for (double[] p : x) {
                atXmin &= Math.abs(p[0] - bb[0]) < tol;
                atXmax &= Math.abs(p[0] - bb[1]) < tol;
                atYmin &= Math.abs(p[1] - bb[2]) < tol;
                atYmax &= Math.abs(p[1] - bb[3]) < tol;
                onCircle &= Math.abs(Math.hypot(p[0] - xc, p[1] - yc) - r) < tol;
            }
            int matches = (atXmin ? 1 : 0) + (atXmax ? 1 : 0) + (atYmin ? 1 : 0) + (atYmax ? 1 : 0) + (onCircle ? 1 : 0);
            if (matches != 1) {
                throw new IllegalStateException("Boundary " + b + " is unclassified or ambiguous (matches=" + matches
                    + "); geometry does not match a channel with an embedded cylinder at (" + xc + ", " + yc + "), R=" + r);
            }
            if (atXmin) inlet.add(b);
            if (atXmax) outlet.add(b);
            if (atYmin) { lateral.add(b); hasBottom = true; }
            if (atYmax) { lateral.add(b); hasTop = true; }
            if (onCircle) cyl.add(b);
        }
        if (inlet.isEmpty() || outlet.isEmpty() || !hasBottom || !hasTop || cyl.isEmpty()) {
            throw new IllegalStateException("Incomplete boundary classification: inlet=" + inlet + ", outlet=" + outlet
                + ", lateral=" + lateral + ", cylinder=" + cyl);
        }
        return new int[][]{toSortedArray(inlet), toSortedArray(outlet), toSortedArray(lateral), toSortedArray(cyl)};
    }

    private static int[] toSortedArray(java.util.List<Integer> list) {
        int[] a = new int[list.size()];
        for (int i = 0; i < a.length; i++) a[i] = list.get(i);
        Arrays.sort(a);
        return a;
    }

    /** Read back selection().entities() of a physics or mesh feature (fail closed). */
    private static int[] featureEntities(Object feat) throws Exception {
        Object sel = feat.getClass().getMethod("selection").invoke(feat);
        Object ents;
        try {
            ents = sel.getClass().getMethod("entities").invoke(sel);
        } catch (NoSuchMethodException e) {
            ents = sel.getClass().getMethod("entities", int.class).invoke(sel, 1);
        }
        int[] a = ((int[]) ents).clone();
        Arrays.sort(a);
        return a;
    }

    private static void requireSameEntities(String what, int[] actual, int[] expected) {
        int[] a = actual.clone(), e = expected.clone();
        Arrays.sort(a);
        Arrays.sort(e);
        if (!Arrays.equals(a, e)) {
            throw new IllegalStateException("Selection readback mismatch for " + what + ": model has "
                + Arrays.toString(a) + ", geometry requires " + Arrays.toString(e));
        }
    }

    private static String doubleArrayToJson(double[] arr) {
        if (arr == null) return "[]";
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            sb.append(arr[i]);
            if (i < arr.length - 1) sb.append(", ");
        }
        sb.append("]");
        return sb.toString();
    }

    static void emitResult(String json) {
        System.out.println(JSON_START);
        System.out.println(json);
        System.out.println(JSON_END);
    }

    static String quote(String s) {
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
                    // Keep structured JSON lossless through locale-dependent batch stdout.
                    if (c < ' ' || c > 126) {
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

    static String getStackTrace(Throwable t) {
        StringWriter sw = new StringWriter();
        t.printStackTrace(new PrintWriter(sw));
        return sw.toString();
    }
}
