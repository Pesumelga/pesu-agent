#!/usr/bin/env python3
"""Build script for PESU Native Host Helper DLL (Phase 9.9.7).

Compiles src/pesu_agent/runtime/native/PesuNonActivatingForm.cs into
build/native/PesuHost.dll using .NET CodeDom at build time.

Runtime CodeDom compilation is strictly forbidden during PESU execution.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_pesu_host")

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CS_PATH = ROOT_DIR / "src" / "pesu_agent" / "runtime" / "native" / "PesuNonActivatingForm.cs"
DEFAULT_OUT_DIR = ROOT_DIR / "build" / "native"
DEFAULT_OUT_DLL = DEFAULT_OUT_DIR / "PesuHost.dll"


def build_pesu_host_dll(
    cs_source_path: Path = DEFAULT_CS_PATH,
    output_dll_path: Path = DEFAULT_OUT_DLL,
) -> Path:
    """Compiles PesuNonActivatingForm.cs to output_dll_path at build time."""
    if not cs_source_path.exists():
        raise FileNotFoundError(f"C# source file not found: {cs_source_path}")

    output_dll_path.parent.mkdir(parents=True, exist_ok=True)
    csharp_code = cs_source_path.read_text(encoding="utf-8")

    logger.info(f"🔨 [BUILD_PESU_HOST] Compiling C# source from: {cs_source_path}")
    logger.info(f"📦 [BUILD_PESU_HOST] Target output DLL: {output_dll_path}")

    import clr
    clr.AddReference("System")
    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")

    import Microsoft.CSharp as cs
    import System.CodeDom.Compiler as cdc

    provider = cs.CSharpCodeProvider()
    params = cdc.CompilerParameters()
    params.ReferencedAssemblies.Add("System.dll")
    params.ReferencedAssemblies.Add("System.Drawing.dll")
    params.ReferencedAssemblies.Add("System.Windows.Forms.dll")

    params.OutputAssembly = str(output_dll_path)
    params.GenerateInMemory = False
    params.CompilerOptions = "/optimize"

    results = provider.CompileAssemblyFromSource(params, csharp_code)

    if results.Errors.HasErrors:
        errors = [str(err) for err in results.Errors]
        logger.error(f"❌ C# Compilation failed with errors: {errors}")
        raise RuntimeError(f"Failed to compile PesuHost.dll: {errors}")

    if not output_dll_path.exists():
        raise FileNotFoundError(f"Compilation succeeded but DLL was not found at {output_dll_path}")

    dll_size = output_dll_path.stat().st_size
    logger.info(f"✅ [BUILD_PESU_HOST] Successfully created {output_dll_path} ({dll_size} bytes)")
    return output_dll_path


def main():
    parser = argparse.ArgumentParser(description="Compile PESU Native Helper DLL")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_CS_PATH,
        help="Path to PesuNonActivatingForm.cs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUT_DLL,
        help="Path to output PesuHost.dll",
    )
    args = parser.parse_args()

    try:
        out = build_pesu_host_dll(args.source, args.output)
        print(f"BUILD_SUCCESS: {out}")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Build error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
