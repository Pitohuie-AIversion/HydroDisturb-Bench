import com.comsol.model.*;
import com.comsol.model.util.*;

public class InspectBlp {
    public static void main(String[] args) throws Exception {
        Model model = ModelUtil.loadCopy("TestInspect", "E:\\cfd_3d\\01_calibration\\c04_re126k\\c04_calibrated_base_mesh1.mph");
        MeshSequence mesh = model.component("comp1").mesh("mesh1");
        MeshFeature blp1 = mesh.feature("bl1").feature("blp1");

        // Re180k values:
        // dy1 = 0.03249 mm, numLayers = 18, r = 1.20 -> Htot = 4.162 mm = 0.004162 m
        double htot = 0.004162;
        int numLayers = 18;
        double growthRate = 1.20;

        blp1.set("inittype", "blhtot");
        blp1.set("blhtot", htot + "[m]");
        blp1.set("blnlayers", numLayers);
        blp1.set("blstretch", growthRate);

        System.out.println("Set blhtot successfully!");
        System.out.println("Running mesh...");
        long t0 = System.nanoTime();
        mesh.run();
        double sec = (System.nanoTime() - t0) / 1e9;
        System.out.println("Mesh rebuilt in " + sec + "s: numElem=" + mesh.getNumElem());
        model.save("E:\\cfd_3d\\02_cases_mph\\re180k_mesh1_base.mph");
        System.out.println("Saved successfully to re180k_mesh1_base.mph!");
    }
}
