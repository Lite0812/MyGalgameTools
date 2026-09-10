from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import frida


def build_agent(module_name: str) -> str:
    target_name = json.dumps(module_name.lower())
    return f"""
'use strict';

const targetName = {target_name};
const reportedModules = new Set();

function normalizeName(value) {{
  if (!value) {{
    return '';
  }}

  const parts = value.split(/[\\/]/);
  return parts[parts.length - 1].toLowerCase();
}}

function normalizeTarget(value) {{
  return normalizeName(value || targetName);
}}

function toRecord(module) {{
  return {{
    name: module.name,
    path: module.path || '',
    base: module.base.toString(),
    size: module.size,
  }};
}}

function findModuleByName(name) {{
  const expectedName = normalizeTarget(name);
  const modules = Process.enumerateModules();

  for (let index = 0; index < modules.length; index += 1) {{
    const module = modules[index];
    if (normalizeName(module.name) === expectedName || normalizeName(module.path) === expectedName) {{
      return module;
    }}
  }}

  return null;
}}

function reportModule(module) {{
  const key = module.base.toString() + ':' + module.size;
  if (reportedModules.has(key)) {{
    return;
  }}

  reportedModules.add(key);
  send({{ type: 'module-loaded', module: toRecord(module) }});
}}

function checkForTarget(name) {{
  const module = findModuleByName(name || targetName);
  if (module) {{
    reportModule(module);
  }}

  return module;
}}

function getExportAddress(moduleName, exportName) {{
  try {{
    return Module.getExportByName(moduleName, exportName);
  }} catch (error) {{
    return null;
  }}
}}

function hookLoader(exportName, isWideString) {{
  const address = getExportAddress('kernel32.dll', exportName);
  if (!address) {{
    return;
  }}

  Interceptor.attach(address, {{
    onEnter(args) {{
      this.requestedPath = '';

      try {{
        this.requestedPath = isWideString ? args[0].readUtf16String() : args[0].readCString();
      }} catch (error) {{
        this.requestedPath = '';
      }}
    }},
    onLeave(retval) {{
      if (retval.isNull()) {{
        return;
      }}

      const requestedName = normalizeName(this.requestedPath);
      if (requestedName !== normalizeTarget(targetName)) {{
        return;
      }}

      const module = findModuleByName(targetName);
      if (module) {{
        reportModule(module);
      }}
    }},
  }});
}}

hookLoader('LoadLibraryA', false);
hookLoader('LoadLibraryW', true);
hookLoader('LoadLibraryExA', false);
hookLoader('LoadLibraryExW', true);

rpc.exports = {{
  findmodule(name) {{
    const module = checkForTarget(name || targetName);
    return module ? toRecord(module) : null;
  }},
  dumpmodule(name) {{
    const module = findModuleByName(name || targetName);
    if (!module) {{
      throw new Error('Target module is not loaded yet.');
    }}

    const bytes = module.base.readByteArray(module.size);
    send({{ type: 'module-bytes', module: toRecord(module) }}, bytes);
    return toRecord(module);
  }},
  listmodules() {{
    return Process.enumerateModules().map(toRecord);
  }},
}};

setImmediate(function () {{
  checkForTarget(targetName);
}});
"""


@dataclass
class ModuleInfo:
    name: str
    path: str
    base: str
    size: int

    @classmethod
    def from_mapping(cls, mapping: dict[str, object]) -> "ModuleInfo":
        size_value = mapping.get("size", 0)
        if isinstance(size_value, bool):
            module_size = int(size_value)
        elif isinstance(size_value, int):
            module_size = size_value
        elif isinstance(size_value, str):
            module_size = int(size_value, 0)
        else:
            module_size = 0

        return cls(
            name=str(mapping.get("name", "")),
            path=str(mapping.get("path", "")),
            base=str(mapping.get("base", "")),
            size=module_size,
        )


class ModuleObserver:
    def __init__(self, module_name: str) -> None:
        self.module_name = module_name.lower()
        self.loaded_module: ModuleInfo | None = None
        self.dumped_module: ModuleInfo | None = None
        self.dumped_bytes: bytes | None = None
        self.last_error: str | None = None

    def on_message(self, message: frida.core.ScriptMessage, _data: bytes | None) -> None:
        message_type = message.get("type")
        if message_type == "send":
            payload = message.get("payload")
            if not isinstance(payload, dict):
                return

            payload_type = payload.get("type")
            if payload_type == "module-bytes":
                module = payload.get("module")
                if isinstance(module, dict):
                    self.dumped_module = ModuleInfo.from_mapping(module)
                self.dumped_bytes = _data or b""
                return

            if payload_type != "module-loaded":
                return

            module = payload.get("module")
            if not isinstance(module, dict):
                return

            module_info = ModuleInfo.from_mapping(module)
            if module_info.name.lower() == self.module_name:
                self.loaded_module = module_info
                print(f"[+] 监控到模块加载: {module_info.name} @ {module_info.base}")
        elif message_type == "error":
            description = message.get("description", "Unknown Frida agent error")
            stack = message.get("stack")
            self.last_error = f"{description}\n{stack}" if stack else str(description)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 Frida 提取 proj.dll，并额外导出一份内存镜像。")
    parser.add_argument(
        "--exe",
        default="mizube.exe",
        help="要启动的游戏可执行文件，默认是当前目录下的 mizube.exe。",
    )
    parser.add_argument(
        "--module-name",
        default="proj.dll",
        help="要监控和导出的 DLL 名称，默认是 proj.dll。",
    )
    parser.add_argument(
        "--out-dir",
        default="frida-dumps",
        help="导出目录，默认是 ./frida-dumps。",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="等待 DLL 加载的超时时间，单位秒，默认 20 秒。",
    )
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="提取完成后不结束游戏进程。",
    )
    parser.add_argument(
        "--skip-memory-dump",
        action="store_true",
        help="只复制磁盘上的原始 DLL，不导出进程内存镜像。",
    )
    return parser.parse_args()


def resolve_exe_path(exe_argument: str) -> Path:
    candidate = Path(exe_argument)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    candidate = candidate.resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f"找不到可执行文件: {candidate}")

    return candidate


def wait_for_module(
    script: frida.core.Script,
    observer: ModuleObserver,
    module_name: str,
    timeout_seconds: float,
) -> ModuleInfo:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        if observer.last_error:
            raise RuntimeError(observer.last_error)

        if observer.loaded_module:
            return observer.loaded_module

        found = script.exports_sync.findmodule(module_name)
        if found:
            return ModuleInfo.from_mapping(found)

        time.sleep(0.2)

    raise TimeoutError(f"在 {timeout_seconds:.1f} 秒内没有等到 {module_name} 加载。")


def copy_raw_module(source_path: str, output_path: Path) -> bool:
    if not source_path:
        return False

    source = Path(source_path)
    if not source.is_file():
        return False

    shutil.copy2(source, output_path)
    return True


def dump_module_image(
    script: frida.core.Script,
    observer: ModuleObserver,
    module_name: str,
    output_path: Path,
) -> ModuleInfo:
    observer.dumped_module = None
    observer.dumped_bytes = None
    module_info = ModuleInfo.from_mapping(script.exports_sync.dumpmodule(module_name))

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if observer.last_error:
            raise RuntimeError(observer.last_error)

        if observer.dumped_bytes is not None:
            output_path.write_bytes(observer.dumped_bytes)
            return observer.dumped_module or module_info

        time.sleep(0.1)

    raise TimeoutError("Frida 已返回模块信息，但没有收到模块内存数据。")


def write_metadata(
    metadata_path: Path,
    exe_path: Path,
    module_info: ModuleInfo,
    raw_module_path: Path,
    raw_module_copied: bool,
    image_dump_path: Path | None,
) -> None:
    metadata = {
        "exe_path": str(exe_path),
        "module": asdict(module_info),
        "raw_module_path": str(raw_module_path),
        "raw_module_copied": raw_module_copied,
        "memory_image_path": str(image_dump_path) if image_dump_path else None,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    args = parse_args()
    exe_path = resolve_exe_path(args.exe)
    output_dir = Path(args.out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_module_path = output_dir / args.module_name
    image_dump_path = output_dir / f"{Path(args.module_name).stem}.memory.bin"
    metadata_path = output_dir / f"{Path(args.module_name).stem}.metadata.json"

    observer = ModuleObserver(args.module_name)
    device = frida.get_local_device()
    session: frida.core.Session | None = None
    script: frida.core.Script | None = None
    pid: int | None = None
    original_cwd = Path.cwd()

    print(f"[*] 启动 {exe_path.name}，等待 {args.module_name} 加载...")

    try:
        os.chdir(exe_path.parent)
        pid = device.spawn([str(exe_path)])
        session = device.attach(pid)
        script = session.create_script(build_agent(args.module_name))
        script.on("message", observer.on_message)
        script.load()
        device.resume(pid)

        module_info = wait_for_module(script, observer, args.module_name, args.timeout)
        print(f"[+] 模块路径: {module_info.path or '<memory only>'}")
        print(f"[+] 模块大小: {module_info.size} bytes")

        raw_module_copied = copy_raw_module(module_info.path, raw_module_path)
        if raw_module_copied:
            print(f"[+] 已复制原始 DLL 到: {raw_module_path}")
        else:
            print("[!] 没有拿到可复制的磁盘文件，将只保留内存镜像。")

        dumped_image_path: Path | None = None
        if not args.skip_memory_dump:
          try:
            dump_module_image(script, observer, args.module_name, image_dump_path)
          except Exception as error:
            print(f"[!] 内存镜像导出失败: {error}")
          else:
            dumped_image_path = image_dump_path
            print(f"[+] 已导出内存镜像到: {image_dump_path}")

        write_metadata(
            metadata_path=metadata_path,
            exe_path=exe_path,
            module_info=module_info,
            raw_module_path=raw_module_path,
            raw_module_copied=raw_module_copied,
            image_dump_path=dumped_image_path,
        )
        print(f"[+] 元数据已写入: {metadata_path}")

        return 0
    finally:
        os.chdir(original_cwd)

        if pid is not None and not args.keep_running:
            try:
                device.kill(pid)
                print("[*] 已结束游戏进程。")
            except frida.ProcessNotFoundError:
                pass

        if session is not None:
            try:
                session.detach()
            except frida.InvalidOperationError:
                pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("用户中断。")