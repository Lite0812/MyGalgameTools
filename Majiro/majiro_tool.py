import argparse
import sys
import os

# Add current directory to path so imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from majiro_disassembler import Disassembler
from majiro_assembler import Assembler

def disassemble(args):
    input_path = args.input
    output_path = args.output
    encoding = args.encoding
    
    if not output_path:
        output_path = os.path.splitext(input_path)[0] + ".mjil"
        
    print(f"Disassembling {input_path} -> {output_path} (encoding: {encoding})")
    
    with open(input_path, 'rb') as f:
        disassembler = Disassembler(encoding=encoding)
        script = disassembler.disassemble_script(f)
        
    with open(output_path, 'w', encoding='utf-8') as f:
        disassembler.print_script(script, f)
        
    print("Done.")

def assemble(args):
    input_path = args.input
    output_path = args.output
    encoding = args.encoding
    other_encoding = args.other_encoding or encoding
    encrypt = args.encrypt
    
    if not output_path:
        output_path = os.path.splitext(input_path)[0] + ".mjo"
        
    print(f"Assembling {input_path} -> {output_path} (encoding: {encoding}, encrypted: {encrypt})")
    
    with open(input_path, 'r', encoding='utf-8') as f:
        text = f.read()
        
    assembler = Assembler(encoding=encoding, other_encoding=other_encoding)
    script, bytecode = assembler.assemble(text)
    
    with open(output_path, 'wb') as f:
        assembler.save(f, script, bytecode, encrypt=encrypt)
        
    print("Done.")

def main():
    parser = argparse.ArgumentParser(description="MajiroTools Python Port")
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Disassemble command
    parser_dis = subparsers.add_parser('disassemble', aliases=['d', 'dis'], help='Disassemble .mjo file')
    parser_dis.add_argument('input', help='Input .mjo file')
    parser_dis.add_argument('output', nargs='?', help='Output .mjil file')
    parser_dis.add_argument('-e', '--encoding', default='shift_jis', help='String encoding (default: shift_jis)')
    parser_dis.set_defaults(func=disassemble)
    
    # Assemble command
    parser_asm = subparsers.add_parser('assemble', aliases=['a', 'asm'], help='Assemble .mjil file')
    parser_asm.add_argument('input', help='Input .mjil file')
    parser_asm.add_argument('output', nargs='?', help='Output .mjo file')
    parser_asm.add_argument('-e', '--encoding', default='shift_jis', help='String encoding (default: shift_jis)')
    parser_asm.add_argument('-x', '--encrypt', action='store_true', help='Encrypt output (MajiroObjX1.000)')
    parser_asm.add_argument('-o', '--other-encoding', default=None, help='Non-text string encoding (default: same as -e)')
    parser_asm.set_defaults(func=assemble)
    
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
