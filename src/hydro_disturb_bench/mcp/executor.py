"""Universal COMSOL Java Execution and Reverse-Engineering Engine.

Provides 100% full-capability API coverage by compiling and executing dynamic
Java code snippets against COMSOL's native Java API (com.comsol.model.*) inside
a crash-isolated subprocess.
"""

import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hydro_disturb_bench.comsol_java import find_comsol_root

from .safety import (
    ERR_COMPILATION_FAILED,
    ERR_EXECUTION_FAILED,
    ERR_MODEL_NOT_FOUND,
    ERR_TIMEOUT_EXCEEDED,
    compute_sha256,
    format_error,
    validate_cow_paths,
)


def _get_java_toolchain(root: Path) -> tuple[Path, Path, str]:
    """Resolves javac, java, and plugins classpath from COMSOL root."""
    java_bin = root / "java" / "win64" / "jre" / "bin"
    javac = java_bin / "javac.exe"
    java = java_bin / "java.exe"
    if not javac.is_file() or not java.is_file():
        raise FileNotFoundError(f"COMSOL bundled OpenJDK toolchain not found at {java_bin}")
    plugins_cp = str(root / "plugins" / "*")
    return javac, java, plugins_cp


_DYNAMIC_EXECUTOR_JAVA_TEMPLATE = """import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import com.sun.jna.*;
import com.comsol.model.*;
import com.comsol.model.util.*;

public class DynamicExecutor {
    private static String decode(String val) {
        if (val == null || val.isEmpty()) return "";
        return new String(Base64.getDecoder().decode(val), StandardCharsets.UTF_8);
    }

    public static void main(String[] args) {
        try {
            System.setOut(new PrintStream(System.out, true, "UTF-8"));
            System.setErr(new PrintStream(System.err, true, "UTF-8"));
            String root = decode(args[0]);
            String inPath = decode(args[1]);
            String outPath = decode(args[2]);
            boolean isGraphics = args.length > 3 && "true".equalsIgnoreCase(decode(args[3]));

            // Windows dynamic DLL discovery via JNA
            NativeLibrary kernel = NativeLibrary.getInstance("kernel32");
            Function add = kernel.getFunction("AddDllDirectory");
            for (String sub : new String[]{"bin/win64", "lib/win64", "java/win64/jre/bin"}) {
                add.invoke(Pointer.class, new Object[]{new WString(root + "/" + sub)});
            }
            kernel.getFunction("SetDefaultDllDirectories").invokeInt(new Object[]{0x1000});

            // Initialize Standalone COMSOL engine
            ModelUtil.initStandalone(isGraphics);
            Model model;
            if ("__BLANK__".equals(inPath)) {
                model = ModelUtil.create("Model");
            } else {
                model = ModelUtil.loadCopy("Model", inPath);
            }

            // Run user/agent dynamic snippet
            SnippetHandler.run(model);

            // Save model if output path requested
            if (outPath != null && !outPath.isEmpty()) {
                File outFile = new File(outPath);
                File parent = outFile.getParentFile();
                if (parent != null && !parent.exists()) {
                    parent.mkdirs();
                }
                model.save(outPath);
            }

            ModelUtil.clear();
            System.out.println("DYNAMIC_EXECUTION_SUCCESS");
            System.exit(0);
        } catch (Throwable t) {
            StringWriter sw = new StringWriter();
            t.printStackTrace(new PrintWriter(sw));
            System.err.println(sw.toString());
            System.exit(1);
        }
    }
}
"""


def execute_java_code(
    java_snippet: str,
    source_model: str | Path | None = None,
    output_model: str | Path | None = None,
    is_graphics: bool = False,
    timeout: int = 300,
) -> dict[str, Any]:
    """Compiles and executes arbitrary COMSOL Java code against a model.

    Args:
        java_snippet: Java statements to execute inside `SnippetHandler.run(Model model)`.
                      Can also be a full class definition if it provides `public static void run(Model model)`.
        source_model: Path to source .mph model file (None or empty creates a blank Model).
        output_model: Path where modified model should be saved (optional).
        is_graphics: Whether offscreen graphics rendering support is required.
        timeout: Subprocess execution timeout in seconds.

    Returns:
        Structured response dictionary.
    """
    # 1. Enforce Copy-on-Write
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    # Check source file existence
    src_path_str = "__BLANK__"
    baseline_hash = None
    if source_model:
        src_p = Path(source_model).resolve()
        if not src_p.is_file():
            return format_error(ERR_MODEL_NOT_FOUND, f"Source model not found: {src_p}")
        src_path_str = str(src_p)
        baseline_hash = compute_sha256(src_p)

    out_path_str = str(Path(output_model).resolve()) if output_model else ""

    # 2. Locate toolchain
    root = find_comsol_root()
    javac, java, plugins_cp = _get_java_toolchain(root)

    # 3. Format SnippetHandler source
    if "class SnippetHandler" in java_snippet:
        snippet_file_content = java_snippet
    else:
        # Wrap snippet in method
        snippet_file_content = f"""import com.comsol.model.*;
import com.comsol.model.util.*;

public class SnippetHandler {{
    public static void run(Model model) throws Exception {{
{java_snippet}
    }}
}}
"""

    with tempfile.TemporaryDirectory(prefix="comsol_mcp_exec_") as temp_dir:
        work = Path(temp_dir)
        (work / "DynamicExecutor.java").write_text(_DYNAMIC_EXECUTOR_JAVA_TEMPLATE, encoding="utf-8")
        (work / "SnippetHandler.java").write_text(snippet_file_content, encoding="utf-8")

        # 4. Compile with javac
        cp = f"{plugins_cp};{work}"
        compile_cmd = [str(javac), "-cp", cp, str(work / "SnippetHandler.java"), str(work / "DynamicExecutor.java")]
        compile_proc = subprocess.run(compile_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if compile_proc.returncode != 0:
            return format_error(
                ERR_COMPILATION_FAILED,
                "Compilation of Java snippet failed. Please review Java syntax and COMSOL API method signatures.",
                compile_proc.stderr or compile_proc.stdout,
            )

        # 5. Execute JVM subprocess
        encoded_args = [
            base64.b64encode(str(root).encode("utf-8")).decode("ascii"),
            base64.b64encode(src_path_str.encode("utf-8")).decode("ascii"),
            base64.b64encode(out_path_str.encode("utf-8")).decode("ascii"),
            base64.b64encode(("true" if is_graphics else "false").encode("utf-8")).decode("ascii"),
        ]

        jvm_args = ["-Xmx4g", "-Xss4m"]
        if is_graphics:
            jvm_args.append("-Dcs.3drend=sw")

        run_cmd = [str(java), *jvm_args, "-cp", cp, "DynamicExecutor", *encoded_args]

        try:
            exec_proc = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return format_error(ERR_TIMEOUT_EXCEEDED, f"Execution timed out after {timeout} seconds.")

        if exec_proc.returncode != 0 or "DYNAMIC_EXECUTION_SUCCESS" not in exec_proc.stdout:
            return format_error(
                ERR_EXECUTION_FAILED,
                "COMSOL Java snippet execution threw an exception.",
                exec_proc.stderr or exec_proc.stdout,
            )

        # 6. Verify baseline immutability
        if baseline_hash and source_model:
            post_hash = compute_sha256(Path(source_model))
            if post_hash != baseline_hash:
                raise RuntimeError("FATAL: Baseline model was modified during execution!")

        res: dict[str, Any] = {
            "status": "SUCCESS",
            "operation": "execute_java",
            "console_output": exec_proc.stdout.replace("DYNAMIC_EXECUTION_SUCCESS", "").strip(),
        }
        if output_model:
            out_p = Path(output_model).resolve()
            if out_p.exists():
                res["output_model"] = str(out_p)
                res["output_size_bytes"] = out_p.stat().st_size
                res["output_sha256"] = compute_sha256(out_p)

        return res


def export_model_to_java(source_model: str | Path, output_java_path: str | Path, timeout: int = 120) -> dict[str, Any]:
    """Reverse-engineers any existing .mph file into complete native Java source code."""
    src_p = Path(source_model).resolve()
    if not src_p.is_file():
        return format_error(ERR_MODEL_NOT_FOUND, f"Source model not found: {src_p}")

    dst_p = Path(output_java_path).resolve()
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    # In COMSOL API, saving to a .java path exports the entire Java source code!
    java_export_code = f"""
        // Simply save to the destination java path
        model.save("{dst_p.as_posix()}");
        System.out.println("Exported model to Java source: {dst_p.as_posix()}");
    """
    res = execute_java_code(
        java_snippet=java_export_code,
        source_model=src_p,
        output_model=None,
        timeout=timeout,
    )

    if res.get("status") == "SUCCESS" and dst_p.exists():
        # Read first few lines of the exported code as preview
        try:
            content = dst_p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = dst_p.read_text(encoding="gbk", errors="replace")

        return {
            "status": "SUCCESS",
            "operation": "export_to_java",
            "source_model": str(src_p),
            "output_java_path": str(dst_p),
            "file_size_bytes": dst_p.stat().st_size,
            "preview_lines": content.splitlines()[:50],
        }
    return res


def create_blank_model(dim: int, name: str, output_model: str | Path, timeout: int = 60) -> dict[str, Any]:
    """Creates a brand new model from scratch with a component and geometry sequence."""
    dst_p = Path(output_model).resolve()
    dst_p.parent.mkdir(parents=True, exist_ok=True)

    snippet = f"""
        model.label("{name}");
        model.component().create("comp1", true);
        model.component("comp1").geom().create("geom1", {dim});
        model.component("comp1").mesh().create("mesh1", "geom1");
        System.out.println("Created blank {dim}D model: {name}");
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=None,
        output_model=dst_p,
        timeout=timeout,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "create_blank_model"
        res["dimension"] = dim
        res["component_tag"] = "comp1"
        res["geometry_tag"] = "geom1"
        res["mesh_tag"] = "mesh1"
    return res
