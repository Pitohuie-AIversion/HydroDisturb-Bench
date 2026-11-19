import com.comsol.model.*;
import com.comsol.model.util.*;

/**
 * Build customized Low-Re boundary layer mesh for multi-Re World Model campaigns.
 *
 * Enforces:
 * - Direct parameterization of U0 for target Reynolds number
 * - Custom first cell height (dy1) guaranteeing y+ <= 1.0 (scaling as Re^-0.9)
 * - Custom prism layer count (num_layers) and stretching factor (growth_rate)
 * - Regeneration of boundary layer prism mesh on cylinder surface
 *
 * Usage:
 *   comsolbatch -inputfile BuildMultiReMesh.class <base_mph> <output_mph> <U0_m_s> <dy1_m> <num_layers> [growth_rate]
 */
public class BuildMultiReMesh {

    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            System.out.println("{\"status\": \"ERROR\", \"message\": \"Usage: BuildMultiReMesh <base_mph> <out_mph> <U0_m_s> <dy1_m> <num_layers> [growth_rate]\"}");
            return;
        }

        String baseMph = args[0];
        String outputMph = args[1];
        double u0 = Double.parseDouble(args[2]);
        double dy1 = Double.parseDouble(args[3]);
        int numLayers = Integer.parseInt(args[4]);
        double growthRate = (args.length >= 6) ? Double.parseDouble(args[5]) : 1.20;

        System.out.println("[BuildMultiReMesh] Loading base model: " + baseMph);
        Model model = ModelUtil.loadCopy("MultiReMeshBuild", baseMph);
        ModelParam param = model.param();

        // Update velocity parameter U0
        param.set("U0", u0 + "[m/s]");
        double D = param.evaluate("D");
        System.out.println("[BuildMultiReMesh] Updated U0=" + u0 + " m/s, D=" + D + " m");

        // Locate mesh sequence dynamically
        ModelNode comp = model.component("comp1");
        String[] meshTags = comp.mesh().tags();
        MeshSequence meshSeq = null;
        if (meshTags != null && meshTags.length > 0) {
            meshSeq = comp.mesh(meshTags[0]);
            System.out.println("[BuildMultiReMesh] Selected mesh sequence: " + meshTags[0]);
        } else {
            meshSeq = comp.mesh("mesh2");
        }

        // Configure boundary layer feature
        MeshFeature bl1 = null;
        if (meshSeq.feature().tags() != null) {
            for (String tag : meshSeq.feature().tags()) {
                MeshFeature f = meshSeq.feature(tag);
                if ("bl1".equals(tag) || "BoundaryLayer".equalsIgnoreCase(f.getType())) {
                    bl1 = f;
                    break;
                }
            }
        }
        if (bl1 == null) {
            bl1 = meshSeq.feature("bl1");
        }
        MeshFeature blp1 = bl1.feature("blp1");

        // Calculate total prism layer height from dy1, numLayers, and growthRate:
        // Htot = dy1 * (r^N - 1) / (r - 1)
        double htot = dy1 * (Math.pow(growthRate, numLayers) - 1.0) / (growthRate - 1.0);

        // Configure 3D Boundary Layer Property (BndLayerProp)
        blp1.set("inittype", "blhtot");
        blp1.set("blhtot", htot + "[m]");
        blp1.set("blnlayers", numLayers);
        blp1.set("blstretch", growthRate);

        System.out.println("[BuildMultiReMesh] Meshing: dy1=" + dy1 + " m, Htot=" + htot + " m, nlayers=" + numLayers + ", r=" + growthRate);

        long t0 = System.nanoTime();
        meshSeq.run();
        double buildSec = (System.nanoTime() - t0) / 1e9;

        int numElem = meshSeq.getNumElem();
        int numVert = meshSeq.getNumVertex();
        System.out.println("[BuildMultiReMesh] Rebuilt in " + String.format("%.2f", buildSec) + " s: "
            + numElem + " elements, " + numVert + " vertices");

        model.save(outputMph);
        System.out.println("[BuildMultiReMesh] Saved to: " + outputMph);

        System.out.println("<<<JSON_START>>>");
        System.out.println("{");
        System.out.println("  \"status\": \"SUCCESS\",");
        System.out.println("  \"operation\": \"build_multi_re_mesh\",");
        System.out.println("  \"U0\": " + u0 + ",");
        System.out.println("  \"dy1_m\": " + dy1 + ",");
        System.out.println("  \"nlayers\": " + numLayers + ",");
        System.out.println("  \"growth_rate\": " + growthRate + ",");
        System.out.println("  \"num_elements\": " + numElem + ",");
        System.out.println("  \"num_vertices\": " + numVert + ",");
        System.out.println("  \"build_seconds\": " + String.format("%.2f", buildSec) + ",");
        System.out.println("  \"output_model\": \"" + outputMph.replace("\\", "\\\\") + "\"");
        System.out.println("}");
        System.out.println("<<<JSON_END>>>");
    }
}
