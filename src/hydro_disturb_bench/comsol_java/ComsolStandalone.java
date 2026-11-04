import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.io.PrintStream;
import com.sun.jna.*;
import com.comsol.model.util.ModelUtil;

/** Supported single-process COMSOL Java API launcher for Windows native DLL lookup. */
public class ComsolStandalone {
    private static String decode(String value) {
        return new String(Base64.getDecoder().decode(value), StandardCharsets.UTF_8);
    }

    public static void main(String[] args) {
        try {
            System.setOut(new PrintStream(System.out, true, "UTF-8"));
            System.setErr(new PrintStream(System.err, true, "UTF-8"));
            String root = decode(args[0]);
            NativeLibrary kernel = NativeLibrary.getInstance("kernel32");
            Function add = kernel.getFunction("AddDllDirectory");
            for (String sub : new String[]{"bin/win64", "lib/win64", "java/win64/jre/bin"}) {
                if (add.invoke(Pointer.class, new Object[]{new WString(root + "/" + sub)}) == null) {
                    throw new IllegalStateException("AddDllDirectory failed: " + sub);
                }
            }
            if (kernel.getFunction("SetDefaultDllDirectories").invokeInt(new Object[]{0x1000}) == 0) {
                throw new IllegalStateException("SetDefaultDllDirectories failed");
            }
            String[] commands = new String[args.length - 1];
            for (int i = 1; i < args.length; i++) commands[i - 1] = decode(args[i]);
            // Image exports require plotting support; no progress window is opened.
            boolean graphics = commands.length > 0 && "export-s1-diagnostics".equals(commands[0]);
            ModelUtil.initStandalone(graphics);
            ModelUtil.showProgress((String) null);
            ComsolBridge.main(commands);
            ModelUtil.clear();
            System.exit(0);
        } catch (Throwable error) {
            error.printStackTrace();
            System.exit(1);
        }
    }
}
