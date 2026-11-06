import com.comsol.model.*;
import com.comsol.model.physics.PhysicsFeature;
import com.comsol.model.util.ModelUtil;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Bounded configurable local source. Frozen pulse commands remain in ComsolBridge. */
public final class ConfigurablePulseBranch {
    private static final String PROTOCOL = "native-restart-consistent-off-v2";
    private static final String[] KEYS = {"event_id", "amplitude_m_s2", "xc_m", "yc_m", "sigma_m",
                                         "direction_deg", "on_s", "off_s"};
    private static final class Raw {
        final String text;
        Raw(String value) { text = value; }
    }
    private static final class StrictProperties extends Properties {
        @Override public synchronized Object put(Object key, Object value) {
            if (containsKey(key)) throw new IllegalArgumentException("Duplicate property: " + key);
            return super.put(key, value);
        }
    }
    static final class Spec {
        final String eventId, propertiesHash;
        final double amplitude, xc, yc, sigma, degrees, on, off, dx, dy;
        Spec(String argument) throws Exception {
            require(argument.startsWith("base64:"), "Properties must be base64 UTF-8 content, not a file path");
            byte[] bytes = Base64.getDecoder().decode(argument.substring(7));
            require(bytes.length > 0 && bytes.length <= 4096, "Properties payload must contain 1..4096 bytes");
            String text = StandardCharsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString();
            Properties properties = new StrictProperties();
            properties.load(new StringReader(text));
            require(properties.stringPropertyNames().equals(new HashSet<>(Arrays.asList(KEYS))), "Unknown or missing pulse properties");
            eventId = properties.getProperty("event_id");
            require(eventId.matches("[A-Za-z0-9][A-Za-z0-9_-]{0,63}"), "Invalid event_id");
            amplitude = number(properties, "amplitude_m_s2");
            xc = number(properties, "xc_m"); yc = number(properties, "yc_m");
            sigma = number(properties, "sigma_m"); degrees = number(properties, "direction_deg");
            on = number(properties, "on_s"); off = number(properties, "off_s");
            // Match Python's Decimal(str(value)) admission. Binary rounding must
            // not turn a permitted rectangle tangency into an out-of-domain event.
            java.math.BigDecimal exactAmplitude = new java.math.BigDecimal(properties.getProperty("amplitude_m_s2"));
            java.math.BigDecimal exactSigma = new java.math.BigDecimal(properties.getProperty("sigma_m"));
            java.math.BigDecimal exactDegrees = new java.math.BigDecimal(properties.getProperty("direction_deg"));
            java.math.BigDecimal exactX = new java.math.BigDecimal(properties.getProperty("xc_m"));
            java.math.BigDecimal exactY = new java.math.BigDecimal(properties.getProperty("yc_m"));
            java.math.BigDecimal exactOn = new java.math.BigDecimal(properties.getProperty("on_s"));
            java.math.BigDecimal exactOff = new java.math.BigDecimal(properties.getProperty("off_s"));
            require(exactAmplitude.signum() >= 0 && exactAmplitude.compareTo(new java.math.BigDecimal("0.01")) <= 0,
                    "Amplitude must lie in [0,0.01] m/s^2");
            require(exactSigma.compareTo(new java.math.BigDecimal("0.005")) >= 0
                    && exactSigma.compareTo(new java.math.BigDecimal("0.02")) <= 0, "Gaussian sigma must lie in [0.005,0.02] m");
            require(exactDegrees.compareTo(new java.math.BigDecimal("-180")) >= 0
                    && exactDegrees.compareTo(new java.math.BigDecimal("180")) <= 0, "Direction must lie in [-180,180] degrees");
            java.math.BigDecimal radius = exactSigma.multiply(new java.math.BigDecimal("3"));
            require(exactX.compareTo(radius) >= 0 && exactX.add(radius).compareTo(new java.math.BigDecimal("0.4")) <= 0
                    && exactY.compareTo(radius) >= 0 && exactY.add(radius).compareTo(new java.math.BigDecimal("0.24")) <= 0,
                    "Center plus three sigma must remain within the channel");
            java.math.BigDecimal distanceSquared = exactX.subtract(new java.math.BigDecimal("0.1")).pow(2)
                .add(exactY.subtract(new java.math.BigDecimal("0.12")).pow(2));
            require(distanceSquared.compareTo(radius.add(new java.math.BigDecimal("0.005")).pow(2)) > 0,
                    "Three-sigma disk touches the cylinder");
            require(exactOn.compareTo(new java.math.BigDecimal("180")) > 0 && exactOn.compareTo(exactOff) < 0
                    && exactOff.compareTo(new java.math.BigDecimal("200")) < 0, "Require 180 < on < off < 200 s");
            require(exactOn.remainder(new java.math.BigDecimal("0.2")).signum() == 0
                    && exactOff.remainder(new java.math.BigDecimal("0.2")).signum() == 0
                    && exactOff.subtract(exactOn).remainder(new java.math.BigDecimal("0.4")).signum() == 0,
                    "On/off must align to 0.2 s and duration to 0.4 s");
            dx = snap(Math.cos(Math.toRadians(degrees))); dy = snap(Math.sin(Math.toRadians(degrees)));
            propertiesHash = ComsolBridge.branchHash(bytes);
        }
        private static double number(Properties p, String key) {
            String value = p.getProperty(key);
            require(value.matches("[+-]?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][+-]?[0-9]+)?"), "Not a decimal number: " + key);
            double result = Double.parseDouble(value);
            require(Double.isFinite(result), "Nonfinite property: " + key);
            return result;
        }
        private static double snap(double value) { return Math.abs(value) < 1e-15 ? 0.0 : value; }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new IllegalArgumentException(message);
    }
    private static Map<String,Object> object(Object... entries) {
        Map<String,Object> result = new LinkedHashMap<>();
        for (int i=0; i<entries.length; i+=2) result.put((String) entries[i], entries[i+1]);
        return result;
    }
    private static String json(Object value) {
        if (value == null) return "null";
        if (value instanceof Raw) return ((Raw)value).text;
        if (value instanceof String) return ComsolBridge.quote((String)value);
        if (value instanceof Number) {
            require(Double.isFinite(((Number)value).doubleValue()), "Nonfinite JSON value");
            return value.toString();
        }
        if (value instanceof Boolean) return value.toString();
        StringJoiner joined = new StringJoiner(",");
        if (value instanceof Map) {
            for (Object item : ((Map<?,?>)value).entrySet()) {
                Map.Entry<?,?> entry = (Map.Entry<?,?>) item;
                joined.add(ComsolBridge.quote(entry.getKey().toString()) + ":" + json(entry.getValue()));
            }
            return "{" + joined + "}";
        }
        if (value instanceof Iterable) for (Object item : (Iterable<?>)value) joined.add(json(item));
        else if (value.getClass().isArray()) for (int i=0; i<java.lang.reflect.Array.getLength(value); i++) joined.add(json(java.lang.reflect.Array.get(value,i)));
        else throw new IllegalArgumentException("Unsupported JSON object");
        return "[" + joined + "]";
    }

    static void handle(String sourcePath, String outputPath, String encodedProperties, boolean solve) {
        String stage = "validate properties";
        Map<String,Object> vectors = object(), derivatives = object();
        Map<String,Object> result = object("status", "ERROR", "operation", solve ? "run_local_pulse" : "inspect_local_pulse",
            "source_model", sourcePath, "output_model", outputPath, "restart_protocol_id", PROTOCOL);
        try {
            Spec spec = new Spec(encodedProperties);
            result.put("event_id", spec.eventId); result.put("properties_sha256", spec.propertiesHash);
            ComsolBridge.branchDistinctPaths(sourcePath, outputPath);
            stage = "load and verify common native checkpoint";
            Model model = ModelUtil.loadCopy("Model", sourcePath);
            String[] tags = ComsolBridge.branchTags(model);
            SolverSequence sol = model.sol(tags[6]), seed = model.sol("sol_branch_seed");
            require(model.param().evaluate("branch_tb") == 180 && model.param().evaluate("branch_source_solnum") == 901
                    && model.param().evaluate("branch_seed_solnum") == 901, "Checkpoint source mapping differs");
            require(seed.getPVals().length == 1001 && ComsolBridge.branchExactTime(seed.getPVals(),180) == 901,
                    "Checkpoint lacks the full native source history");
            String before = ComsolBridge.branchSnapshot(model,tags), meshBefore = ComsolBridge.branchMeshFingerprint(model,tags);
            result.put("geometry", geometry(model,tags)); result.put("material", material(model,tags));
            double[] nativeU = seed.getU(901);
            String seedHash = ComsolBridge.branchHash(ComsolBridge.branchVectorBytes(nativeU));
            String derivativeHash = ComsolBridge.branchHash(ComsolBridge.branchVectorBytes(seed.getUDot(901)));
            require(seed.comments().contains("float64_le_sha256=" + seedHash)
                    && seed.comments().contains("udot_float64_le_sha256=" + derivativeHash)
                    && seed.comments().contains("copy_method=copySolution_full_native_history"), "Native seed provenance differs");
            ComsolBridge.branchRequireVector(seed,901,180,seedHash,nativeU.length);
            ComsolBridge.branchRequireDerivative(seed,901,derivativeHash,nativeU.length);
            vectors.put("seed",new Raw(ComsolBridge.branchVectorJson(seed,901)));
            derivatives.put("seed",new Raw(ComsolBridge.branchDerivativeJson(seed,901)));
            String sourceMapping = ComsolBridge.branchDofMapping(sol);
            ComsolBridge.branchCheckTimeSettings(sol.feature(tags[8]));
            stage = "install configurable force";
            Map<String,Object> actualPulse = installParameters(model,spec);
            com.comsol.model.physics.Physics physics = model.component(tags[0]).physics(tags[3]);
            require(!Arrays.asList(physics.feature().tags()).contains("local_vf"), "Local force already exists");
            PhysicsFeature force = physics.create("local_vf","VolumeForce",2);
            force.selection().set(new int[]{1});
            String spatial = "exp(-((x-local_xc)^2+(y-local_yc)^2)/(2*local_sigma^2))";
            String gate = "if((t>local_on)&&(t<local_off),sin(pi*(t-local_on)/(local_off-local_on))^2,0)";
            String[] expectedForce = {"rho_f*local_A*local_dx*"+spatial+"*"+gate,
                                      "rho_f*local_A*local_dy*"+spatial+"*"+gate,"0[N/m^3]"};
            force.set("F",expectedForce);
            String[] actualForce = force.getStringArray("F");
            require(Arrays.equals(actualForce,expectedForce) && force.isActive()
                    && Arrays.equals(force.selection().entities(),new int[]{1}), "Actual force feature differs");
            ComsolBridge.branchConfigureInitialization(model,tags);
            SolverFeature time = sol.feature(tags[8]), vars = sol.feature(tags[7]);
            require("bweuler".equals(time.getString("consistent")), "Checkpoint consistent initialization differs");
            time.set("consistent","off");
            require("off".equals(time.getString("consistent")), "Restart protocol did not read back");
            int selectedIndex = ComsolBridge.branchManualSolutionIndex(vars);
            SolverSequence selectedSource = model.sol(vars.getString("initsol"));
            ComsolBridge.branchRequireVector(selectedSource,selectedIndex,180,seedHash,nativeU.length);
            ComsolBridge.branchRequireDerivative(selectedSource,selectedIndex,derivativeHash,nativeU.length);
            vectors.put("input",new Raw(ComsolBridge.branchVectorJson(selectedSource,selectedIndex)));
            derivatives.put("input",new Raw(ComsolBridge.branchDerivativeJson(selectedSource,selectedIndex)));
            stage = "Variables validation probe";
            sol.run(tags[7],false);
            String initializedMapping = ComsolBridge.branchDofMapping(sol);
            require(sourceMapping.equals(initializedMapping), "DOF mapping changed after local force installation");
            require(Arrays.equals(sol.getPVals(1),new double[]{180}) && sol.getU(1).length == nativeU.length,
                    "Variables probe time or DOF count differs");
            vectors.put("variables_probe",new Raw(ComsolBridge.branchVectorJson(sol,1)));
            derivatives.put("variables_probe",new Raw(ComsolBridge.branchDerivativeJson(sol,1)));
            result.put("source_dof_mapping",new Raw(sourceMapping));
            result.put("initialized_dof_mapping",new Raw(initializedMapping));
            result.put("initialization_difference_by_field",new Raw(ComsolBridge.branchFieldDifferences(sol,nativeU,sol.getU(1))));
            stage = "prescribed source diagnostics without Time solve";
            result.put("actual_pulse",actualPulse);
            result.put("force_evidence",sourceDiagnostics(model,tags,actualForce,spec));
            String configured = ComsolBridge.branchSnapshot(model,tags);
            require(meshBefore.equals(ComsolBridge.branchMeshFingerprint(model,tags)), "Mesh changed before Time");
            ComsolBridge.branchCheckTimeSettings(time);
            result.put("initialization_readback",object("control",vars.getString("control"),"initmethod",vars.getString("initmethod"),
                "initsol",vars.getString("initsol"),"initsoluse",vars.getString("initsoluse"),"solnum",vars.getString("solnum"),
                "manualsolnum",vars.getDoubleArray("manualsolnum"),"native_seed_time",seed.getPVals(901),
                "native_seed_sha256",seedHash,"native_seed_udot_sha256",derivativeHash));
            if (solve) {
                stage = "Time solve 180..200 s";
                require("off".equals(time.getString("consistent")), "Restart protocol changed before Time");
                System.out.println("LOCAL_PULSE_NATIVE_INIT_READBACK="+json(result.get("initialization_readback")));
                System.out.println("LOCAL_PULSE_TIME_START event_id="+spec.eventId+" properties_sha256="+spec.propertiesHash);
                sol.runAll(false);
                System.out.println("LOCAL_PULSE_TIME_FINISHED");
                double[] saved = sol.getPVals();
                require(saved.length == 101, "Expected 101 output frames");
                for (int i=0;i<saved.length;i++) {
                    require(Double.isFinite(saved[i]) && Math.abs(saved[i]-(180+.2*i)) <= 1e-10, "Output time schedule differs");
                    ComsolBridge.branchVectorBytes(sol.getU(i+1));
                }
                vectors.put("solver_first",new Raw(ComsolBridge.branchVectorJson(sol,1)));
                derivatives.put("solver_first",new Raw(ComsolBridge.branchDerivativeJson(sol,1)));
                result.put("actual_force_evaluation",actualForceEvidence(model,tags,actualForce,spec,saved));
            }
            stage = "validate and save independent output";
            String meshAfter = ComsolBridge.branchMeshFingerprint(model,tags);
            require(meshBefore.equals(meshAfter), "Mesh changed during operation");
            ComsolBridge.branchCheckTimeSettings(time);
            require("off".equals(time.getString("consistent")), "Restart protocol changed");
            require(json(actualPulse).equals(json(readParameters(model,spec))), "Pulse parameters changed during operation");
            require(Arrays.equals(actualForce,force.getStringArray("F")) && force.isActive(), "Force changed during operation");
            StringBuilder feature = new StringBuilder();
            ComsolBridge.appendNativeFeature(feature,force,tags[0]+"/"+tags[3]+"/"+force.tag());
            result.put("force_feature",new Raw(feature.toString()));
            result.put("before",new Raw(before)); result.put("configured",new Raw(configured));
            result.put("after",new Raw(ComsolBridge.branchSnapshot(model,tags)));
            result.put("mesh_before",new Raw(meshBefore)); result.put("mesh_after",new Raw(meshAfter));
            result.put("native_vectors",vectors); result.put("native_derivatives",derivatives);
            result.put("seed_solnum",901); result.put("seed_frame_count",seed.getPVals().length);
            result.put("native_copy_method","copySolution_full_native_history"); result.put("seed_provenance",seed.comments());
            result.put("dataset_tag",tags[9]); result.put("solution_tag",tags[6]);
            result.put("solver_times",sol.getPVals()); result.put("actual_times",sol.getPVals());
            result.put("time_solver_executed",solve);
            result.put("native_input_role","live Variables initsol/manualsolnum selects full native seed; variables_probe is diagnostic only");
            result.put("time_solver_log",solve ? time.getString("message") : "");
            model.save(outputPath);
            result.put("status","SUCCESS");
            ComsolBridge.emitResult(json(result));
        } catch (Throwable error) {
            result.put("status","ERROR");
            result.put("error_code",stage.equals("validate properties") ? "INVALID_PARAMETER" : "MODEL_VALIDATION_FAILED");
            result.put("message","Local pulse failed at "+stage+": "+error.getMessage());
            result.put("details",ComsolBridge.getStackTrace(error));
            result.put("partial_native_vectors",vectors); result.put("partial_native_derivatives",derivatives);
            ComsolBridge.emitResult(json(result));
        }
    }

    private static Map<String,Object> installParameters(Model model, Spec spec) {
        ModelParam p = model.param();
        for (String name:p.varnames()) require(!name.startsWith("local_"), "Checkpoint already contains local parameters");
        p.set("local_A",spec.amplitude+"[m/s^2]"); p.set("local_xc",spec.xc+"[m]"); p.set("local_yc",spec.yc+"[m]");
        p.set("local_sigma",spec.sigma+"[m]"); p.set("local_theta",spec.degrees+"[deg]");
        p.set("local_dx",Double.toString(spec.dx)); p.set("local_dy",Double.toString(spec.dy));
        p.set("local_on",spec.on+"[s]"); p.set("local_off",spec.off+"[s]");
        return readParameters(model,spec);
    }
    private static Map<String,Object> readParameters(Model model, Spec spec) {
        ModelParam p = model.param();
        String[] names={"local_A","local_xc","local_yc","local_sigma","local_theta","local_dx","local_dy","local_on","local_off"};
        String[] units={"m/s^2","m","m","m","deg","1","1","s","s"};
        double[] expected={spec.amplitude,spec.xc,spec.yc,spec.sigma,spec.degrees,spec.dx,spec.dy,spec.on,spec.off};
        Map<String,Object> parameters=object();
        double[] actual=new double[names.length];
        for(int i=0;i<names.length;i++) {
            actual[i]=p.evaluate(names[i]+"/1["+units[i]+"]");
            require(Double.isFinite(actual[i]) && Math.abs(actual[i]-expected[i])<=1e-12, "Actual parameter differs: "+names[i]);
            parameters.put(names[i],object("expression",p.get(names[i]),"value",p.evaluate(names[i]),"unit",p.evaluateUnit(names[i]),
                "value_in_declared_unit",actual[i],"declared_unit",units[i]));
        }
        return object("amplitude_m_s2",actual[0],"xc_m",actual[1],"yc_m",actual[2],"sigma_m",actual[3],
            "direction_deg",actual[4],"direction",new double[]{actual[5],actual[6]},"on_s",actual[7],"off_s",actual[8],
            "parameters",parameters);
    }
    private static Map<String,Object> geometry(Model model,String[] tags) {
        GeomSequence geom=model.component(tags[0]).geom(tags[1]);
        double[] bounds=geom.getBoundingBox(), expected={0,.4,0,.24};
        require(bounds.length==4,"Invalid geometry bounds");
        for(int i=0;i<4;i++) require(Math.abs(bounds[i]-expected[i])<=1e-12,"Channel geometry differs");
        GeomFeature circle=null;
        for(String tag:geom.feature().tags()) if("Circle".equals(geom.feature(tag).getType())) {
            require(circle==null,"Ambiguous cylinder"); circle=geom.feature(tag);
        }
        require(circle!=null,"Missing cylinder");
        double[] center=circle.getDoubleArray("pos"); double radius=circle.getDouble("r");
        require(center.length==2 && Math.abs(center[0]-.1)<=1e-12 && Math.abs(center[1]-.12)<=1e-12
                && Math.abs(radius-.005)<=1e-12,"Cylinder geometry differs");
        return object("component_tag",tags[0],"geometry_tag",tags[1],"length_unit",geom.lengthUnit(),"bounds",bounds,
            "circle",object("feature_tag",circle.tag(),"center",center,"radius",radius));
    }
    private static Map<String,Object> material(Model model,String[] tags) {
        ModelNode comp=model.component(tags[0]); ModelParam p=model.param();
        require(comp.material().tags().length==1,"Ambiguous material");
        Material mat=comp.material(comp.material().tags()[0]);
        String[] density=mat.propertyGroup("def").getStringArray("density");
        String[] viscosity=mat.propertyGroup("def").getStringArray("dynamicviscosity");
        require(Arrays.equals(density,new String[]{"rho_f"}) && Arrays.equals(viscosity,new String[]{"mu_f"}),"Material expressions differ");
        PhysicsFeature fluid=null;
        for(String tag:comp.physics(tags[3]).feature().tags()) {
            PhysicsFeature node=comp.physics(tags[3]).feature(tag);
            List<String> properties=Arrays.asList(node.properties());
            if(properties.contains("rho") && properties.contains("mu")) { require(fluid==null,"Ambiguous fluid properties"); fluid=node; }
        }
        require(fluid!=null && "rho_f".equals(fluid.getString("rho")) && "mu_f".equals(fluid.getString("mu")),"Physics material expressions differ");
        double rho=p.evaluate("rho_f/1[kg/m^3]"), mu=p.evaluate("mu_f/1[Pa*s]");
        require(Math.abs(rho-998.2)<=1e-10 && Math.abs(mu-.001004)<=1e-15,"Material constants differ");
        return object("material_tag",mat.tag(),"density_expression",density,"dynamicviscosity_expression",viscosity,
            "physics_feature_tag",fluid.tag(),"physics_rho_expression",fluid.getString("rho"),"physics_mu_expression",fluid.getString("mu"),
            "rho_f",object("expression",p.get("rho_f"),"value",p.evaluate("rho_f"),"unit",p.evaluateUnit("rho_f")),
            "mu_f",object("expression",p.get("mu_f"),"value",p.evaluate("mu_f"),"unit",p.evaluateUnit("mu_f")));
    }

    private static String[] substituted(String[] force,double[] times,boolean coordinates) {
        List<String> expressions=new ArrayList<>();
        for(double tau:times) for(String expression:force) expressions.add("subst("+expression+",t,"+Double.toString(tau)+"[s])");
        if(coordinates) expressions.addAll(Arrays.asList("t","x","y"));
        return expressions.toArray(new String[0]);
    }
    private static Map<String,Object> point(Model model,String[] tags,String prefix,double x,double y,String[] expressions,int[] indices) {
        DatasetFeature point=model.result().dataset().create(prefix+"_point","CutPoint2D");
        point.set("data",tags[9]); point.set("pointx",x); point.set("pointy",y);
        NumericalFeature eval=model.result().numerical().create(prefix+"_eval","EvalPoint");
        eval.set("data",point.tag()); eval.set("expr",expressions);
        eval.set("solrepresentation","solnum"); eval.set("innerinput","manual"); eval.set("solnum",indices);
        double[][] values=ComsolBridge.branchResultMatrix(eval.getReal(),expressions.length,indices.length);
        String[] units=eval.getStringArray("unit");
        require(units.length==expressions.length,"Force point unit count differs");
        for(int i=0;i<units.length-3;i++) require("N/m^3".equals(units[i]),"Actual force point unit is not N/m^3");
        require("s".equals(units[units.length-3]) && "m".equals(units[units.length-2]) && "m".equals(units[units.length-1]),"Actual time/coordinate units differ");
        for(int i=0;i<indices.length;i++) require(Math.abs(values[values.length-2][i]-x)<=1e-12
                && Math.abs(values[values.length-1][i]-y)<=1e-12,"Actual force point coordinates differ");
        return object("tag",eval.tag(),"type",eval.getType(),"dataset_tag",eval.getString("data"),
            "point_dataset_tag",point.tag(),"point_dataset_type",point.getType(),"point_source_dataset",point.getString("data"),
            "requested_coordinates",new double[]{x,y},"solrepresentation",eval.getString("solrepresentation"),
            "innerinput",eval.getString("innerinput"),"solnum",eval.getIntArray("solnum"),
            "expressions",eval.getStringArray("expr"),"units",units,"values_by_expression",values);
    }
    private static Map<String,Object> integral(Model model,String[] tags,String prefix,String[] expressions,int[] indices) {
        NumericalFeature eval=model.result().numerical().create(prefix+"_integral","IntSurface");
        eval.set("data",tags[9]); eval.selection().set(new int[]{1}); eval.set("expr",expressions);
        eval.set("solrepresentation","solnum"); eval.set("innerinput","manual"); eval.set("solnum",indices);
        double[][] values=ComsolBridge.branchResultMatrix(eval.getReal(),expressions.length,indices.length);
        String[] units=eval.getStringArray("unit");
        require(units.length==expressions.length,"Force integral unit count differs");
        for(String unit:units) require("N/m".equals(unit),"Actual source integral unit is not N/m");
        return object("tag",eval.tag(),"type",eval.getType(),"dataset_tag",eval.getString("data"),"selection",eval.selection().entities(),
            "solrepresentation",eval.getString("solrepresentation"),"innerinput",eval.getString("innerinput"),
            "solnum",eval.getIntArray("solnum"),"expressions",eval.getStringArray("expr"),"units",units,"values_by_expression",values);
    }
    private static Map<String,Object> sourceDiagnostics(Model model,String[] tags,String[] force,Spec spec) {
        double[] times={180,spec.on,(spec.on+spec.off)/2,spec.off,200};
        String[] expr=substituted(force,times,true); List<Object> points=new ArrayList<>();
        double[][] coordinates={{spec.xc,spec.yc},{spec.xc+spec.sigma,spec.yc},{spec.xc,spec.yc+spec.sigma}};
        String[] roles={"center","x_plus_sigma","y_plus_sigma"};
        for(int i=0;i<3;i++) {
            Map<String,Object> evidence=point(model,tags,"local_diag_"+i,coordinates[i][0],coordinates[i][1],expr,new int[]{1});
            evidence.put("role",roles[i]);
            double[][] values=(double[][])evidence.get("values_by_expression");
            require(Math.abs(values[values.length-3][0]-180)<=1e-12,"Diagnostic native solution time differs");
            points.add(evidence);
        }
        return object("expression_source_feature","local_vf","source_expressions",force,"dataset_tag",tags[9],
            "solution_tag",model.result().dataset(tags[9]).getString("solution"),"native_solnum",1,"native_solution_time",180.,
            "diagnostic_times",times,"diagnostic_time_semantics","prescribed_source_time_substitution; not future solved states",
            "points",points,"integral",integral(model,tags,"local_diag",substituted(force,times,false),new int[]{1}));
    }
    private static Map<String,Object> actualForceEvidence(Model model,String[] tags,String[] force,Spec spec,double[] times) {
        int[] indices=new int[times.length]; for(int i=0;i<indices.length;i++) indices[i]=i+1;
        String[] expr={force[0],force[1],force[2],"t","x","y"};
        Map<String,Object> p=point(model,tags,"local_actual",spec.xc,spec.yc,expr,indices);
        double[][] values=(double[][])p.get("values_by_expression");
        for(int i=0;i<times.length;i++) require(Math.abs(values[3][i]-times[i])<=1e-10,"Actual source evaluation time differs");
        Map<String,Object> totals=integral(model,tags,"local_actual",force,indices);
        totals.put("force_per_depth_by_component",totals.get("values_by_expression"));
        return object("expression_source_feature","local_vf","dataset_tag",tags[9],
            "solution_tag",model.result().dataset(tags[9]).getString("solution"),"actual_times",values[3],
            "point_evaluation",p,"integral",totals);
    }
}
