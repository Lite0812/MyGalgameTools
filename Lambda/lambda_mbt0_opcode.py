#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lambda MBT0/SAM pseudo-op definitions.

These files are not a classic VM bytecode stream.  DID_MES_00_DAT is an MBT0
message table and DID_PURI_PARA_DAT is a SAM parameter table.  We still keep a
small opcode module so the disassembler and assembler share one truth source
for pseudo-ops and fixed structure constants.
"""

from __future__ import annotations


DEFAULT_ENCODING = "cp932"

MAGIC_MBT0 = b"MBT0"
MAGIC_SAM = b"SAM\x00"

MBT0_HEADER_SIZE = 0x14
MBT0_SPECIAL_TAIL_PTRS = 3

SAM_HEADER_SIZE = 0x28
SAM_RECORD_OFFSET = 0x28
SAM_RECORD_SIZE = 0x884C

PSEUDO_OPS = {
    ".format",
    ".source_size",
    ".encoding",
    ".u32",
    ".ptr",
    ".bytes",
    ".zero",
    ".zstr",
    ".record",
    ".endrecord",
}

