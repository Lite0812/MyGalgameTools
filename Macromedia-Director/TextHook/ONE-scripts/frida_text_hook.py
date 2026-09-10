#!/usr/bin/env python3
"""
Hook Director/Win32 text drawing APIs with Frida and dump visible text as JSON Lines.

Targets ANSI text APIs commonly used by this game's Director Xtras:
  - gdi32!TextOutA
  - gdi32!ExtTextOutA
  - user32!DrawTextA / DrawTextExA
  - user32!SetWindowTextA

Also hooks W variants as a fallback. ANSI bytes are decoded in Python with cp932 by
default, so Japanese Shift-JIS text can be recovered even on non-Japanese systems.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

try:
    import frida
except ImportError:
    print("[!] Python package 'frida' is not installed. Install with: pip install frida frida-tools", file=sys.stderr)
    raise


JS_CODE = r"""
'use strict';

const MAX_AUTO_STRING = 4096;

function nowMs() {
  return Date.now();
}

function ptrIsNull(p) {
  return p === null || p.isNull();
}

function readBytes(ptrValue, length) {
  if (ptrIsNull(ptrValue)) return null;
  try {
    let n = Number(length);
    if (n < 0 || !Number.isFinite(n)) {
      const s = ptrValue.readCString(MAX_AUTO_STRING);
      n = s.length;
    }
    if (n <= 0 || n > MAX_AUTO_STRING) return null;
    const buf = ptrValue.readByteArray(n);
    if (buf === null) return null;
    return base64ArrayBuffer(buf);
  } catch (e) {
    return null;
  }
}

function readUtf16(ptrValue, wcharCount) {
  if (ptrIsNull(ptrValue)) return null;
  try {
    let n = Number(wcharCount);
    if (n < 0 || !Number.isFinite(n)) {
      return ptrValue.readUtf16String(MAX_AUTO_STRING / 2);
    }
    if (n <= 0 || n > MAX_AUTO_STRING / 2) return null;
    return ptrValue.readUtf16String(n);
  } catch (e) {
    return null;
  }
}

function base64ArrayBuffer(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let output = '';
  let i;
  for (i = 0; i + 2 < bytes.length; i += 3) {
    output += alphabet[bytes[i] >> 2];
    output += alphabet[((bytes[i] & 3) << 4) | (bytes[i + 1] >> 4)];
    output += alphabet[((bytes[i + 1] & 15) << 2) | (bytes[i + 2] >> 6)];
    output += alphabet[bytes[i + 2] & 63];
  }
  if (i < bytes.length) {
    output += alphabet[bytes[i] >> 2];
    if (i + 1 < bytes.length) {
      output += alphabet[((bytes[i] & 3) << 4) | (bytes[i + 1] >> 4)];
      output += alphabet[(bytes[i + 1] & 15) << 2];
      output += '=';
    } else {
      output += alphabet[(bytes[i] & 3) << 4];
      output += '==';
    }
  }
  return output;
}

function rectToObject(rectPtr) {
  if (ptrIsNull(rectPtr)) return null;
  try {
    return {
      left: rectPtr.readS32(),
      top: rectPtr.add(4).readS32(),
      right: rectPtr.add(8).readS32(),
      bottom: rectPtr.add(12).readS32()
    };
  } catch (e) {
    return null;
  }
}

const apiHitCounts = {};

function noteApiHit(api, ptrValue, length, extra) {
  const count = (apiHitCounts[api] || 0) + 1;
  apiHitCounts[api] = count;
  if (count <= 5 || count === 10 || count === 100 || count === 1000) {
    send(Object.assign({
      kind: 'api-hit',
      api: api,
      count: count,
      ptr: ptrValue ? ptrValue.toString() : null,
      length: Number(length),
      timestamp_ms: nowMs(),
      thread_id: Process.getCurrentThreadId()
    }, extra || {}));
  }
}

function sendAnsi(api, ptrValue, length, extra) {
  noteApiHit(api, ptrValue, length, extra);
  const b64 = readBytes(ptrValue, length);
  if (b64 === null) {
    send(Object.assign({
      kind: 'text-draw',
      api: api,
      encoding: 'ansi',
      timestamp_ms: nowMs(),
      raw_b64: null,
      text: '',
      js_reject_reason: 'read_bytes_failed',
      ptr: ptrValue ? ptrValue.toString() : null,
      length: Number(length),
      thread_id: Process.getCurrentThreadId()
    }, extra || {}));
    return;
  }
  send(Object.assign({
    kind: 'text-draw',
    api: api,
    encoding: 'ansi',
    timestamp_ms: nowMs(),
    raw_b64: b64,
    length: Number(length),
    thread_id: Process.getCurrentThreadId()
  }, extra || {}));
}

function sendWide(api, ptrValue, wcharCount, extra) {
  noteApiHit(api, ptrValue, wcharCount, extra);
  const text = readUtf16(ptrValue, wcharCount);
  if (text === null) {
    send(Object.assign({
      kind: 'text-draw',
      api: api,
      encoding: 'utf-16le',
      timestamp_ms: nowMs(),
      text: '',
      js_reject_reason: 'read_utf16_failed',
      ptr: ptrValue ? ptrValue.toString() : null,
      length: Number(wcharCount),
      thread_id: Process.getCurrentThreadId()
    }, extra || {}));
    return;
  }
  send(Object.assign({
    kind: 'text-draw',
    api: api,
    encoding: 'utf-16le',
    timestamp_ms: nowMs(),
    text: text,
    length: Number(wcharCount),
    thread_id: Process.getCurrentThreadId()
  }, extra || {}));
}

const hookedExports = {};

function findExportCompat(moduleName, exportName) {
  try {
    if (typeof Module.findExportByName === 'function') {
      const addr = Module.findExportByName(moduleName, exportName);
      if (addr !== null) return addr;
    }
  } catch (e) {}
  try {
    if (typeof Module.getExportByName === 'function') {
      return Module.getExportByName(moduleName, exportName);
    }
  } catch (e) {}
  try {
    if (typeof Process.findModuleByName === 'function') {
      const m = Process.findModuleByName(moduleName);
      if (m !== null) {
        if (typeof m.findExportByName === 'function') {
          const addr = m.findExportByName(exportName);
          if (addr !== null) return addr;
        }
        if (typeof m.getExportByName === 'function') {
          return m.getExportByName(exportName);
        }
      }
    }
  } catch (e) {}
  try {
    if (typeof Process.getModuleByName === 'function') {
      const m = Process.getModuleByName(moduleName);
      if (m !== null) {
        if (typeof m.findExportByName === 'function') {
          const addr = m.findExportByName(exportName);
          if (addr !== null) return addr;
        }
        if (typeof m.getExportByName === 'function') {
          return m.getExportByName(exportName);
        }
      }
    }
  } catch (e) {}
  try {
    if (typeof Module.findGlobalExportByName === 'function') {
      const addr = Module.findGlobalExportByName(exportName);
      if (addr !== null) return addr;
    }
  } catch (e) {}
  try {
    if (typeof Module.getGlobalExportByName === 'function') {
      return Module.getGlobalExportByName(exportName);
    }
  } catch (e) {}
  return null;
}

function hookExport(moduleName, exportName, callbacks, attempt) {
  attempt = attempt || 0;
  const key = moduleName + '!' + exportName;
  if (hookedExports[key]) return;

  const addr = findExportCompat(moduleName, exportName);

  // When spawning a process, Frida injects while the process is still suspended;
  // user32/gdi32 may not be mapped yet. Keep retrying until the loader brings
  // those DLLs in, then attach the hooks before normal rendering starts.
  if (addr === null) {
    if (attempt === 0) {
      send({ kind: 'hook-pending', module: moduleName, export: exportName });
    }
    if (attempt < 200) {
      setTimeout(function () {
        hookExport(moduleName, exportName, callbacks, attempt + 1);
      }, 50);
    } else {
      send({ kind: 'hook-missing', module: moduleName, export: exportName });
    }
    return;
  }

  hookedExports[key] = true;
  Interceptor.attach(addr, callbacks);
  send({ kind: 'hooked', module: moduleName, export: exportName, address: addr.toString() });
}

hookExport('gdi32.dll', 'TextOutA', {
  onEnter(args) {
    sendAnsi('TextOutA', args[3], args[4].toInt32(), {
      hdc: args[0].toString(),
      x: args[1].toInt32(),
      y: args[2].toInt32()
    });
  }
});

hookExport('gdi32.dll', 'ExtTextOutA', {
  onEnter(args) {
    sendAnsi('ExtTextOutA', args[5], args[6].toInt32(), {
      hdc: args[0].toString(),
      x: args[1].toInt32(),
      y: args[2].toInt32(),
      options: args[3].toUInt32(),
      rect: rectToObject(args[4])
    });
  }
});

hookExport('user32.dll', 'DrawTextA', {
  onEnter(args) {
    sendAnsi('DrawTextA', args[1], args[2].toInt32(), {
      hdc: args[0].toString(),
      rect: rectToObject(args[3]),
      format: args[4].toUInt32()
    });
  }
});

hookExport('user32.dll', 'DrawTextExA', {
  onEnter(args) {
    sendAnsi('DrawTextExA', args[1], args[2].toInt32(), {
      hdc: args[0].toString(),
      rect: rectToObject(args[3]),
      format: args[4].toUInt32()
    });
  }
});

hookExport('user32.dll', 'SetWindowTextA', {
  onEnter(args) {
    sendAnsi('SetWindowTextA', args[1], -1, {
      hwnd: args[0].toString()
    });
  }
});

// Fallback hooks in case another path uses Unicode APIs.
hookExport('gdi32.dll', 'TextOutW', {
  onEnter(args) {
    sendWide('TextOutW', args[3], args[4].toInt32(), {
      hdc: args[0].toString(),
      x: args[1].toInt32(),
      y: args[2].toInt32()
    });
  }
});

hookExport('gdi32.dll', 'ExtTextOutW', {
  onEnter(args) {
    sendWide('ExtTextOutW', args[5], args[6].toInt32(), {
      hdc: args[0].toString(),
      x: args[1].toInt32(),
      y: args[2].toInt32(),
      options: args[3].toUInt32(),
      rect: rectToObject(args[4])
    });
  }
});

hookExport('user32.dll', 'DrawTextW', {
  onEnter(args) {
    sendWide('DrawTextW', args[1], args[2].toInt32(), {
      hdc: args[0].toString(),
      rect: rectToObject(args[3]),
      format: args[4].toUInt32()
    });
  }
});

hookExport('user32.dll', 'DrawTextExW', {
  onEnter(args) {
    sendWide('DrawTextExW', args[1], args[2].toInt32(), {
      hdc: args[0].toString(),
      rect: rectToObject(args[3]),
      format: args[4].toUInt32()
    });
  }
});

// Measurement APIs are often called with the original string before an engine
// renders/caches text into a bitmap. These are very useful for Director Xtras.
hookExport('gdi32.dll', 'GetTextExtentPoint32A', {
  onEnter(args) {
    sendAnsi('GetTextExtentPoint32A', args[1], args[2].toInt32(), {
      hdc: args[0].toString()
    });
  }
});

hookExport('gdi32.dll', 'GetTextExtentPointA', {
  onEnter(args) {
    sendAnsi('GetTextExtentPointA', args[1], args[2].toInt32(), {
      hdc: args[0].toString()
    });
  }
});

hookExport('gdi32.dll', 'GetTextExtentExPointA', {
  onEnter(args) {
    sendAnsi('GetTextExtentExPointA', args[1], args[2].toInt32(), {
      hdc: args[0].toString(),
      max_extent: args[3].toInt32()
    });
  }
});

hookExport('gdi32.dll', 'GetTabbedTextExtentA', {
  onEnter(args) {
    sendAnsi('GetTabbedTextExtentA', args[1], args[2].toInt32(), {
      hdc: args[0].toString()
    });
  }
});

hookExport('gdi32.dll', 'GetTextExtentPoint32W', {
  onEnter(args) {
    sendWide('GetTextExtentPoint32W', args[1], args[2].toInt32(), {
      hdc: args[0].toString()
    });
  }
});

hookExport('gdi32.dll', 'GetTextExtentExPointW', {
  onEnter(args) {
    sendWide('GetTextExtentExPointW', args[1], args[2].toInt32(), {
      hdc: args[0].toString(),
      max_extent: args[3].toInt32()
    });
  }
});

// Less-common text drawing APIs.
hookExport('gdi32.dll', 'TabbedTextOutA', {
  onEnter(args) {
    sendAnsi('TabbedTextOutA', args[3], args[4].toInt32(), {
      hdc: args[0].toString(),
      x: args[1].toInt32(),
      y: args[2].toInt32()
    });
  }
});

hookExport('gdi32.dll', 'TabbedTextOutW', {
  onEnter(args) {
    sendWide('TabbedTextOutW', args[3], args[4].toInt32(), {
      hdc: args[0].toString(),
      x: args[1].toInt32(),
      y: args[2].toInt32()
    });
  }
});

hookExport('user32.dll', 'DrawStateA', {
  onEnter(args) {
    const flags = args[9].toUInt32();
    if ((flags & 0x000f) === 0x0001) { // DST_TEXT
      sendAnsi('DrawStateA', args[3], args[4].toInt32(), {
        hdc: args[0].toString(),
        x: args[5].toInt32(),
        y: args[6].toInt32(),
        cx: args[7].toInt32(),
        cy: args[8].toInt32(),
        flags: flags
      });
    }
  }
});

hookExport('user32.dll', 'DrawStateW', {
  onEnter(args) {
    const flags = args[9].toUInt32();
    if ((flags & 0x000f) === 0x0001) { // DST_TEXT
      sendWide('DrawStateW', args[3], args[4].toInt32(), {
        hdc: args[0].toString(),
        x: args[5].toInt32(),
        y: args[6].toInt32(),
        cx: args[7].toInt32(),
        cy: args[8].toInt32(),
        flags: flags
      });
    }
  }
});

hookExport('user32.dll', 'GrayStringA', {
  onEnter(args) {
    sendAnsi('GrayStringA', args[3], args[4].toInt32(), {
      hdc: args[0].toString(),
      x: args[5].toInt32(),
      y: args[6].toInt32(),
      width: args[7].toInt32(),
      height: args[8].toInt32()
    });
  }
});

hookExport('user32.dll', 'GrayStringW', {
  onEnter(args) {
    sendWide('GrayStringW', args[3], args[4].toInt32(), {
      hdc: args[0].toString(),
      x: args[5].toInt32(),
      y: args[6].toInt32(),
      width: args[7].toInt32(),
      height: args[8].toInt32()
    });
  }
});

// Encoding conversion channel: if the engine converts Shift-JIS/CP932 text to
// Unicode before rendering/caching, this catches the original multibyte string.
hookExport('kernel32.dll', 'MultiByteToWideChar', {
  onEnter(args) {
    sendAnsi('MultiByteToWideChar', args[2], args[3].toInt32(), {
      code_page: args[0].toUInt32(),
      flags: args[1].toUInt32()
    });
  }
});

hookExport('KernelBase.dll', 'MultiByteToWideChar', {
  onEnter(args) {
    sendAnsi('MultiByteToWideChar', args[2], args[3].toInt32(), {
      code_page: args[0].toUInt32(),
      flags: args[1].toUInt32()
    });
  }
});
"""

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WHITESPACE_RE = re.compile(r"\s+")


def is_pure_ascii(text: str) -> bool:
    return bool(text) and all(ord(ch) < 0x80 for ch in text)


def looks_illegal(text: str) -> bool:
    if not text:
        return True
    if "�" in text:
        return True
    if CONTROL_RE.search(text):
        return True
    stripped = text.strip()
    if not stripped:
        return True
    # Avoid obvious binary/garbage strings: too many private/surrogate/control-like chars.
    bad = 0
    visible = 0
    for ch in stripped:
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF or o in (0xFFFE, 0xFFFF):
            bad += 1
        elif ch.isprintable() or ch in "\r\n\t":
            visible += 1
        else:
            bad += 1
    return bad > 0 or visible == 0


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return WHITESPACE_RE.sub(" ", text).strip()


def decode_ansi(raw: bytes, encodings: Iterable[str]) -> Tuple[Optional[str], Optional[str]]:
    raw = raw.split(b"\x00", 1)[0]
    if not raw:
        return None, None
    for enc in encodings:
        try:
            text = raw.decode(enc, errors="strict")
        except UnicodeDecodeError:
            continue
        text = normalize_text(text)
        if not looks_illegal(text):
            return text, enc
    return None, None


def decode_wide_text(text: str) -> str:
    return normalize_text(text)


class JsonlWriter:
    def __init__(self, path: Path, flush: bool = True):
        self.path = path
        self.flush = flush
        self.fp = path.open("a", encoding="utf-8", newline="\n")

    def write(self, obj: Dict[str, Any]) -> None:
        self.fp.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
        if self.flush:
            self.fp.flush()

    def close(self) -> None:
        self.fp.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hook Win32/Director text drawing APIs and dump text to JSONL.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--exe", help="Path to executable to spawn, e.g. oneWin.exe")
    target.add_argument("--pid", type=int, help="Attach to an existing process ID")
    target.add_argument("--process-name", help="Attach to an existing process by name, e.g. oneWin.exe")
    parser.add_argument("--cwd", default=None, help="Working directory when spawning --exe. Defaults to exe directory.")
    parser.add_argument("--out", default="frida_text_output.jsonl", help="Output JSON Lines path.")
    parser.add_argument("--encoding", action="append", default=None,
                        help="ANSI decoding candidate. Can be repeated. Default: cp932, shift_jis, utf-8, mbcs.")
    parser.add_argument("--keep-ascii", action="store_true", help="Do not filter pure ASCII strings.")
    parser.add_argument("--include-raw", action="store_true", help="Include raw_b64 in JSON output.")
    parser.add_argument("--dump-rejects", action="store_true",
                        help="Also write rejected hook events with accepted=false and reject_reason.")
    parser.add_argument("--dedupe-ms", type=int, default=0,
                        help="Suppress duplicate text/api/position events within this many milliseconds. Default: 0 off.")
    parser.add_argument("--print", dest="print_stdout", action="store_true", help="Also print accepted JSON records to stdout.")
    return parser.parse_args()


def attach_or_spawn(args: argparse.Namespace):
    device = frida.get_local_device()
    pid = None
    spawned = False
    if args.exe:
        exe_path = str(Path(args.exe).resolve())
        cwd = args.cwd or str(Path(exe_path).parent)
        print(f"[*] Spawning {exe_path} cwd={cwd}", file=sys.stderr)
        pid = device.spawn([exe_path], cwd=cwd)
        spawned = True
        session = device.attach(pid)
    elif args.pid:
        print(f"[*] Attaching pid={args.pid}", file=sys.stderr)
        session = device.attach(args.pid)
    else:
        print(f"[*] Attaching process-name={args.process_name}", file=sys.stderr)
        session = device.attach(args.process_name)
    return device, session, pid, spawned


def main() -> int:
    args = parse_args()
    # Do not fall back to Windows "mbcs" by default: on this machine the ANSI
    # code page is CP936, which turns invalid Shift-JIS byte pairs into plausible
    # Chinese characters and creates false positives. Pass --encoding mbcs
    # explicitly if you really want that behavior.
    encodings = args.encoding or ["cp932", "shift_jis", "utf-8"]

    out_path = Path(args.out)
    writer = JsonlWriter(out_path)
    seen: Dict[Tuple[Any, ...], int] = {}

    device, session, pid, spawned = attach_or_spawn(args)

    def on_message(message: Dict[str, Any], data: Any) -> None:
        nonlocal seen
        if message.get("type") == "error":
            print("[frida-error]", message.get("stack") or message, file=sys.stderr)
            return
        if message.get("type") != "send":
            print("[frida-message]", message, file=sys.stderr)
            return
        payload = message.get("payload") or {}
        kind = payload.get("kind")
        if kind == "hooked":
            print(f"[+] hooked {payload.get('module')}!{payload.get('export')} @ {payload.get('address')}", file=sys.stderr)
            return
        if kind == "hook-pending":
            print(f"[*] waiting for {payload.get('module')}!{payload.get('export')}", file=sys.stderr)
            return
        if kind == "hook-missing":
            print(f"[-] missing {payload.get('module')}!{payload.get('export')}", file=sys.stderr)
            return
        if kind == "api-hit":
            print(
                f"[hit] {payload.get('api')} count={payload.get('count')} "
                f"ptr={payload.get('ptr')} len={payload.get('length')}",
                file=sys.stderr,
            )
            return
        if kind != "text-draw":
            return

        record = dict(payload)
        raw_b64 = record.pop("raw_b64", None)
        text = record.get("text")
        used_encoding = record.get("encoding")

        reject_reason = None
        if record.get("js_reject_reason"):
            reject_reason = record.get("js_reject_reason")
        elif raw_b64 is not None:
            try:
                raw = base64.b64decode(raw_b64)
            except Exception:
                raw = b""
                reject_reason = "base64_decode_failed"
            if reject_reason is None:
                text, used_encoding = decode_ansi(raw, encodings)
                if text is None:
                    reject_reason = "ansi_decode_or_illegal_failed"
                else:
                    record["encoding"] = used_encoding
            if args.include_raw or args.dump_rejects:
                record["raw_b64"] = raw_b64
                record["raw_hex_prefix"] = raw[:64].hex(" ")
        else:
            text = decode_wide_text(str(text or ""))
            if looks_illegal(text):
                reject_reason = "wide_illegal_or_empty"

        if reject_reason is None and not args.keep_ascii and is_pure_ascii(text):
            reject_reason = "pure_ascii"

        record["text"] = text or ""
        record["unix_time"] = time.time()
        if reject_reason is not None:
            if not args.dump_rejects:
                return
            record["accepted"] = False
            record["reject_reason"] = reject_reason
            writer.write(record)
            if args.print_stdout:
                print(json.dumps(record, ensure_ascii=False))
            return
        record["accepted"] = True

        if args.dedupe_ms > 0:
            key = (record.get("api"), record.get("text"), record.get("x"), record.get("y"), json.dumps(record.get("rect"), sort_keys=True))
            now_ms = int(record.get("timestamp_ms") or 0)
            prev = seen.get(key)
            if prev is not None and now_ms - prev < args.dedupe_ms:
                return
            seen[key] = now_ms
            # Occasionally prune old keys.
            if len(seen) > 10000:
                cutoff = now_ms - max(args.dedupe_ms * 4, 5000)
                seen = {k: v for k, v in seen.items() if v >= cutoff}

        writer.write(record)
        if args.print_stdout:
            print(json.dumps(record, ensure_ascii=False))

    script = session.create_script(JS_CODE)
    script.on("message", on_message)
    script.load()

    if spawned and pid is not None:
        device.resume(pid)
        print(f"[*] Resumed pid={pid}", file=sys.stderr)

    print(f"[*] Writing JSONL to {out_path.resolve()}", file=sys.stderr)
    print("[*] Press Ctrl+C to stop.", file=sys.stderr)

    stop = False

    def handle_sigint(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        while not stop:
            time.sleep(0.2)
    finally:
        print("\n[*] Detaching...", file=sys.stderr)
        try:
            script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass
        writer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
