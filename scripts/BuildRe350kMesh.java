import com.comsol.model.*;
import com.comsol.model.util.*;

public class BuildRe350kMesh {
    public static void main(String[] args) throws Exception {
        System.out.println("[BuildRe350kMesh] Loading base model c04_calibrated_base_mesh1.mph...");
        Model model = ModelUtil.loadCopy("BuildRe350k", "E:\\cfd_3d\\01_calibration\\c04_re126k\\c04_calibrated_base_mesh1.mph");
        
        // Update velocity for Re=350,000 (U0 = 1.38705 m/s)
        model.param().set("U0", "1.38705[m/s]");
        System.out.println("[BuildRe350kMesh] Updated U0 to 1.38705[m/s]");

        MeshSequence mesh = model.component("comp1").mesh("mesh1");
        MeshFeature blp1 = mesh.feature("bl1").feature("blp1");

        // Re350k values from multi_re_6plus2_matrix_plan.json:
        // dy1 = 0.01786 mm, numLayers = 21, r = 1.20 -> Htot = 4.018 mm = 0.004018 m
        double htot = 0.004018;
        int numLayers = 21;
        double growthRate = 1.20;

        blp1.set("inittype", "blhtot");
        blp1.set("blhtot", htot + "[m]");
        blp1.set("blnlayers", numLayers);
        blp1.set("blstretch", growthRate);

        System.out.println("[BuildRe350kMesh] Set blhtot=" + htot + "m, nlayers=" + numLayers + ", stretch=" + growthRate);
        System.out.println("[BuildRe350kMesh] Rebuilding mesh...");
        long t0 = System.nanoTime();
        mesh.run();
        double sec = (System.nanoTime() - t0) / 1e9;
        System.out.println("[BuildRe350kMesh] Mesh rebuilt in " + String.format("%.2f", sec) + "s: numElem=" + mesh.getNumElem());
        
        String outPath = "E:\\cfd_3d\\02_cases_mph\\re350k_mesh1_base.mph";
        model.save(outPath);
        System.out.println("[BuildRe350kMesh] Saved successfully to: " + outPath);
    }
}
