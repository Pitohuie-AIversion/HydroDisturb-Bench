import com.comsol.model.*;
import com.comsol.model.util.*;

public class BuildRe200kMesh {
    public static void main(String[] args) throws Exception {
        System.out.println("[BuildRe200kMesh] Loading base model c04_calibrated_base_mesh1.mph...");
        Model model = ModelUtil.loadCopy("BuildRe200k", "E:\\cfd_3d\\01_calibration\\c04_re126k\\c04_calibrated_base_mesh1.mph");
        
        // Update velocity for Re=200,000 (U0 = 0.7926 m/s)
        model.param().set("U0", "0.7926[m/s]");
        System.out.println("[BuildRe200kMesh] Updated U0 to 0.7926[m/s]");

        MeshSequence mesh = model.component("comp1").mesh("mesh1");
        MeshFeature blp1 = mesh.feature("bl1").feature("blp1");

        // Re200k values from multi_re_6plus2_matrix_plan.json:
        // dy1 = 0.02955 mm, numLayers = 19, r = 1.20 -> Htot = 4.572 mm = 0.004572 m
        double htot = 0.004572;
        int numLayers = 19;
        double growthRate = 1.20;

        blp1.set("inittype", "blhtot");
        blp1.set("blhtot", htot + "[m]");
        blp1.set("blnlayers", numLayers);
        blp1.set("blstretch", growthRate);

        System.out.println("[BuildRe200kMesh] Set blhtot=" + htot + "m, nlayers=" + numLayers + ", stretch=" + growthRate);
        System.out.println("[BuildRe200kMesh] Rebuilding mesh...");
        long t0 = System.nanoTime();
        mesh.run();
        double sec = (System.nanoTime() - t0) / 1e9;
        System.out.println("[BuildRe200kMesh] Mesh rebuilt in " + String.format("%.2f", sec) + "s: numElem=" + mesh.getNumElem());
        
        String outPath = "E:\\cfd_3d\\02_cases_mph\\re200k_mesh1_base.mph";
        model.save(outPath);
        System.out.println("[BuildRe200kMesh] Saved successfully to: " + outPath);
    }
}
