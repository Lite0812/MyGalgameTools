# -*- coding: utf-8 -*-
r"""
SS文本封回脚本 - 将JSON/ASM/TXT文本封回到.ss文件
支持注音文本处理，自动拆分合并的分句

注音格式: @r注音@被注音文本@
句子分隔符: @n（用于分隔同一行的多个分句）
原始换行符: \n（直接保留）

用法: python ssTextPacker.py <Scene\> <DATA\> [Output\] [options]
选项:
  -v: 详细输出模式

支持的格式:
  1. 完整JSON格式: {file, string_count, entries: [{line, name, text, parts}]}
  2. 纯净JSON格式: [{name, message}, ...]
  3. ASM格式: 反汇编文件，解析@STR[N]=...行
  4. 全量TXT: 每行一个字符串，按索引顺序
  5. 翻译TXT: 只导回◆编号◆内容 行
"""

import sys
import os
import glob
import struct
import json
import re
import shutil


def Decrypt(string, l, k):
    key = 28807
    localKey = key * k % 65536
    newString = b''
    for n in range(0, l):
        newString += struct.pack('H', localKey ^ struct.unpack('H', string[n*2:n*2+2])[0])
    return newString


def parse_asm_file(asm_path):
    """
    解析ASM文件，提取所有字符串修改
    优先从TRING TABLE部分解析完整字符串
    格式: @STR[N]=完整内容
    
    返回: {str_id: 文本内容, ...}
    """
    strings = {}
    
    with open(asm_path, 'r', encoding='utf-8-sig') as f:  # 使用utf-8-sig自动处理BOM
        for line in f:
            line = line.rstrip('\n\r')
            
            # 解析字符串表格式: @STR[N]=content
            if line.startswith('@STR['):
                match = re.match(r'@STR\[(\d+)\]=(.*)$', line)
                if match:
                    str_id = int(match.group(1))
                    text = match.group(2)
                    # 反转义换行符
                    text = text.replace('\\n', '\n').replace('\\r', '\r').replace('\\\\', '\\')
                    strings[str_id] = text
    
    return strings


def pack_asm_to_ss(ss_path, asm_strings, verbose=False):
    """
    将ASM中的字符串封回到SS文件
    
    参数:
        ss_path: 原始SS文件路径
        asm_strings: {str_id: text} 字典
        verbose: 是否详细输出
    
    返回:
        (ss_file, modified_count)
    """
    ss_file = SSFile(ss_path)
    modified_count = 0
    
    for str_id, new_text in asm_strings.items():
        if 0 <= str_id < ss_file.str_idx_cnt:
            old_text = ss_file.get_string(str_id)
            if new_text != old_text:
                ss_file.set_string(str_id, new_text)
                modified_count += 1
                if verbose:
                    old_preview = old_text[:30] + '...' if len(old_text) > 30 else old_text
                    new_preview = new_text[:30] + '...' if len(new_text) > 30 else new_text
                    print(f"    [{str_id}] {old_preview} -> {new_preview}")
    
    return ss_file, modified_count


def detect_json_format(json_data):
    """
    检测JSON格式类型
    返回: 'full' (完整格式) 或 'pure' (纯净格式)
    """
    if isinstance(json_data, list):
        # 纯净格式: [{name, message}, ...]
        return 'pure'
    elif isinstance(json_data, dict) and 'entries' in json_data:
        # 完整格式: {file, entries: [...]}
        return 'full'
    else:
        # 可能是空字典或其他情况，默认为full尝试处理，或抛出异常
        if not json_data: 
            return 'empty'
        raise ValueError("无法识别的JSON格式")


def extract_entries_from_ss(ss_path):
    """
    从SS文件提取原始结构信息，用于纯净模式封回
    返回与完整格式相同的entries结构
    """
    # 导入提取器（在函数内部导入以避免循环引用）
    import ssTextExtractor as extractor
    
    result = extractor.extract_ss_file(ss_path, export_all=False, verbose=False)
    if result:
        return result.get('entries', [])
    return []


def merge_pure_with_original(pure_entries, original_entries):
    """
    将纯净格式JSON与原始结构合并
    纯净格式: [{name, message}, ...]
    原始结构: [{line, name, name_str_id, text, parts, type?}, ...]
    """
    # 如果纯净条目为空，直接返回空
    if not pure_entries:
        return {'entries': []}
        
    if len(pure_entries) != len(original_entries):
        raise ValueError(f"条目数量不匹配: 纯净JSON有{len(pure_entries)}条，原始SS有{len(original_entries)}条")
    
    merged_entries = []
    for pure, original in zip(pure_entries, original_entries):
        merged = {
            'line': original.get('line'),
            'name': pure.get('name', ''),  # 使用纯净格式的name
            'name_str_id': original.get('name_str_id'),  # 保留原始nname_str_id用于封回
            'text': pure.get('message', ''),  # 用纯净格式的message替换text
            'parts': original.get('parts', [])
        }
        # 保留type字段（如果原始条目有）
        if original.get('type'):
            merged['type'] = original['type']
        merged_entries.append(merged)
    
    return {'entries': merged_entries}


class SSFile:
    """SS文件解析器和写入器"""
    
    def __init__(self, filename):
        self.filename = filename
        self.fileSize = os.path.getsize(filename)
        
        with open(filename, 'rb') as f:
            # 读取头部
            self.header_size = struct.unpack('I', f.read(4))[0]
            header_data = f.read(128)
            self.header = list(struct.unpack('32I', header_data))
            
            # 解析头部字段
            self.scn_ofs = self.header[0]
            self.scn_size = self.header[1]
            self.str_idx_ofs = self.header[2]
            self.str_idx_cnt = self.header[3]
            self.str_list_ofs = self.header[4]
            self.str_cnt = self.header[5]
            self.label_ofs = self.header[6]
            self.label_cnt = self.header[7]
            self.z_label_ofs = self.header[8]
            self.z_label_cnt = self.header[9]
            self.cmd_label_ofs = self.header[10]
            self.cmd_label_cnt = self.header[11]
            self.scn_prop_ofs = self.header[12]
            self.scn_prop_cnt = self.header[13]
            self.scn_prop_idx_ofs = self.header[14]
            self.scn_prop_idx_cnt = self.header[15]
            self.scn_prop_name_ofs = self.header[16]
            self.scn_prop_name_cnt = self.header[17]
            self.scn_cmd_ofs = self.header[18]
            self.scn_cmd_cnt = self.header[19]
            self.scn_cmd_idx_ofs = self.header[20]
            self.scn_cmd_idx_cnt = self.header[21]
            self.scn_cmd_name_ofs = self.header[22]
            self.scn_cmd_name_cnt = self.header[23]
            self.call_prop_idx_ofs = self.header[24]
            self.call_prop_idx_cnt = self.header[25]
            self.call_prop_name_ofs = self.header[26]
            self.call_prop_name_cnt = self.header[27]
            self.namae_ofs = self.header[28]
            self.namae_cnt = self.header[29]
            self.read_flag_ofs = self.header[30]
            self.read_flag_cnt = self.header[31]
            
            # 读取字符串索引
            f.seek(self.str_idx_ofs)
            self.str_offset = []
            self.str_length = []
            for n in range(self.str_idx_cnt):
                self.str_offset.append(struct.unpack('I', f.read(4))[0])
                self.str_length.append(struct.unpack('I', f.read(4))[0])
            
            # 读取加密的字符串
            self.strings = []
            for i in range(self.str_idx_cnt):
                if self.str_length[i] == 0:
                    self.strings.append(b'')
                    continue
                f.seek(self.str_list_ofs + self.str_offset[i] * 2)
                self.strings.append(f.read(self.str_length[i] * 2))
            
            # 读取字节码
            f.seek(self.scn_ofs)
            self.bytecode = bytearray(f.read(self.scn_size))
            
            # 读取其余数据段
            f.seek(self.label_ofs)
            self.labels_data = f.read(self.label_cnt * 4)
            
            f.seek(self.z_label_ofs)
            self.z_labels_data = f.read(self.z_label_cnt * 4)
            
            f.seek(self.cmd_label_ofs)
            self.cmd_labels_data = f.read(self.cmd_label_cnt * 8)
            
            f.seek(self.scn_prop_ofs)
            self.scn_prop_data = f.read(self.scn_prop_cnt * 8)
            
            f.seek(self.scn_prop_idx_ofs)
            self.scn_prop_idx_data = f.read(self.scn_prop_idx_cnt * 8)
            
            if self.scn_prop_name_cnt > 0:
                f.seek(self.scn_prop_name_ofs)
                prop_name_size = (self.scn_cmd_ofs - self.scn_prop_name_ofs) if self.scn_cmd_cnt > 0 else 0
                self.scn_prop_name_data = f.read(prop_name_size) if prop_name_size > 0 else b''
            else:
                self.scn_prop_name_data = b''
            
            f.seek(self.scn_cmd_ofs)
            self.scn_cmd_data = f.read(self.scn_cmd_cnt * 4)
            
            f.seek(self.scn_cmd_idx_ofs)
            self.scn_cmd_idx_data = f.read(self.scn_cmd_idx_cnt * 8)
            
            if self.scn_cmd_name_cnt > 0:
                f.seek(self.scn_cmd_name_ofs)
                cmd_name_size = (self.call_prop_idx_ofs - self.scn_cmd_name_ofs) if self.call_prop_idx_cnt > 0 else 0
                self.scn_cmd_name_data = f.read(cmd_name_size) if cmd_name_size > 0 else b''
            else:
                self.scn_cmd_name_data = b''
            
            f.seek(self.call_prop_idx_ofs)
            self.call_prop_idx_data = f.read(self.call_prop_idx_cnt * 8)
            
            if self.call_prop_name_cnt > 0:
                f.seek(self.call_prop_name_ofs)
                call_prop_name_size = (self.namae_ofs - self.call_prop_name_ofs) if self.namae_cnt > 0 else 0
                self.call_prop_name_data = f.read(call_prop_name_size) if call_prop_name_size > 0 else b''
            else:
                self.call_prop_name_data = b''
            
            f.seek(self.namae_ofs)
            self.namae_data = f.read(self.namae_cnt * 4)
            
            f.seek(self.read_flag_ofs)
            self.read_flag_data = f.read(self.read_flag_cnt * 4)
    
    def get_string(self, idx):
        """获取解密后的字符串"""
        if idx < 0 or idx >= len(self.strings):
            return ""
        if self.str_length[idx] == 0:
            return ""
        decrypted = Decrypt(self.strings[idx], self.str_length[idx], idx)
        try:
            return decrypted.decode('UTF-16')
        except:
            return ""
    
    def set_string(self, idx, text):
        """设置字符串（会自动加密）"""
        if idx < 0 or idx >= len(self.strings):
            return False
        self.str_length[idx] = len(text)
        if len(text) == 0:
            self.strings[idx] = b''
        else:
            encoded = text.encode('UTF-16')[2:]  # 移除BOM
            self.strings[idx] = Decrypt(encoded, len(text), idx)
        return True
    
    def write(self, filename):
        """写入修改后的SS文件"""
        with open(filename, 'wb') as f:
            # 写入头部占位
            f.write(struct.pack('I', self.header_size))
            f.write(bytes(128))
            
            # 写入字符串索引
            new_str_ofs = f.tell()
            new_offset = 0
            for i in range(self.str_idx_cnt):
                f.write(struct.pack('I', new_offset))
                f.write(struct.pack('I', self.str_length[i]))
                new_offset += self.str_length[i]
            
            # 写入字符串数据
            new_str_list_ofs = f.tell()
            for i in range(self.str_idx_cnt):
                f.write(self.strings[i])
            
            # 写入字节码
            new_scn_ofs = f.tell()
            f.write(bytes(self.bytecode))
            
            # 写入其余数据段
            new_label_ofs = f.tell()
            f.write(self.labels_data)
            
            new_z_label_ofs = f.tell()
            f.write(self.z_labels_data)
            
            new_cmd_label_ofs = f.tell()
            f.write(self.cmd_labels_data)
            
            new_scn_prop_ofs = f.tell()
            f.write(self.scn_prop_data)
            
            new_scn_prop_idx_ofs = f.tell()
            f.write(self.scn_prop_idx_data)
            
            new_scn_prop_name_ofs = f.tell()
            f.write(self.scn_prop_name_data)
            
            new_scn_cmd_ofs = f.tell()
            f.write(self.scn_cmd_data)
            
            new_scn_cmd_idx_ofs = f.tell()
            f.write(self.scn_cmd_idx_data)
            
            new_scn_cmd_name_ofs = f.tell()
            f.write(self.scn_cmd_name_data)
            
            new_call_prop_idx_ofs = f.tell()
            f.write(self.call_prop_idx_data)
            
            new_call_prop_name_ofs = f.tell()
            f.write(self.call_prop_name_data)
            
            new_namae_ofs = f.tell()
            f.write(self.namae_data)
            
            new_read_flag_ofs = f.tell()
            f.write(self.read_flag_data)
            
            # 更新头部
            new_header = list(self.header)
            new_header[0] = new_scn_ofs
            new_header[1] = len(self.bytecode)
            new_header[2] = new_str_ofs
            new_header[4] = new_str_list_ofs
            new_header[6] = new_label_ofs
            new_header[8] = new_z_label_ofs
            new_header[10] = new_cmd_label_ofs
            new_header[12] = new_scn_prop_ofs
            new_header[14] = new_scn_prop_idx_ofs
            new_header[16] = new_scn_prop_name_ofs
            new_header[18] = new_scn_cmd_ofs
            new_header[20] = new_scn_cmd_idx_ofs
            new_header[22] = new_scn_cmd_name_ofs
            new_header[24] = new_call_prop_idx_ofs
            new_header[26] = new_call_prop_name_ofs
            new_header[28] = new_namae_ofs
            new_header[30] = new_read_flag_ofs
            
            # 写入更新后的头部
            f.seek(4)
            f.write(struct.pack('32I', *new_header))


def parse_ruby_text(text):
    """
    解析带注音的文本
    格式: @r注音@被注音文本@
    返回: (纯文本, 注音) 或 (text, None)
    """
    # 匹配 @r注音@被注音文本@ 格式
    match = re.match(r'^@r(.+?)@(.+?)@$', text)
    if match:
        ruby = match.group(1)
        base_text = match.group(2)
        return (base_text, ruby)
    return (text, None)


def split_merged_text(merged_text, original_parts):
    """
    将合并的文本拆分回原始分句
    
    参数:
        merged_text: 合并后的文本（分句用@n分隔）
        original_parts: 原始分句信息列表
    
    返回:
        [{str_id, text, ruby, ruby_str_id}, ...] - 每个分句的信息
    
    特殊处理:
        - 如果用户删除了所有@n和注音，整个文本会放到第一个分句
        - 其他分句设为空字符串
    """
    # 先清理注音格式，提取纯文本和注音信息
    clean_text, ruby_map = extract_ruby_from_text(merged_text)
    
    # 使用 @n 作为句子分隔符进行拆分
    parts = clean_text.split('@n') if '@n' in clean_text else [clean_text]
    
    result = []
    
    # 情况1: 用户删除了所有分句（没有@n），把整个文本放到第一个分句
    if len(parts) == 1 and len(original_parts) > 1:
        # 整个文本放到第一个分句
        for i, part_info in enumerate(original_parts):
            if i == 0:
                result.append({
                    'str_id': part_info['str_id'],
                    'text': parts[0],
                    'ruby': None,  # 合并后不保留注音
                    'ruby_str_id': part_info.get('ruby_str_id')
                })
            else:
                # 其他分句设为空
                result.append({
                    'str_id': part_info['str_id'],
                    'text': '',
                    'ruby': None,
                    'ruby_str_id': part_info.get('ruby_str_id')
                })
        return result
    
    # 情况2: 正常拆分
    # 确保分句数量匹配
    if len(parts) != len(original_parts):
        if len(parts) < len(original_parts):
            # 翻译文本分句少于原始，用空字符串填充
            parts.extend([''] * (len(original_parts) - len(parts)))
        else:
            # 翻译文本分句多于原始，合并多余的到最后一个
            while len(parts) > len(original_parts):
                parts[-2] = parts[-2] + parts[-1]
                parts.pop()
    
    for i, part_info in enumerate(original_parts):
        str_id = part_info['str_id']
        text = parts[i] if i < len(parts) else ''
        
        # 从注音映射中获取对应的注音（如果有）
        ruby = ruby_map.get(i)
        
        result.append({
            'str_id': str_id,
            'text': text,
            'ruby': ruby,
            'ruby_str_id': part_info.get('ruby_str_id')
        })
    
    return result


def extract_ruby_from_text(text):
    """
    从文本中提取注音信息
    格式: @r注音@被注音文本@
    
    返回: (清理后的文本, {分句索引: 注音})
    
    注意: 这里使用 @n 作为句子分隔符计算分句索引
    """
    import re
    ruby_map = {}
    
    # 匹配 @r注音@文本@ 格式
    pattern = r'@r(.+?)@(.+?)@'
    
    # 计算每个注音在哪个分句中
    clean_parts = []
    current_pos = 0
    part_index = 0
    
    for match in re.finditer(pattern, text):
        # 注音前的文本
        before = text[current_pos:match.start()]
        # 计算这段文本中有多少个 @n（句子分隔符）
        part_index += before.count('@n')
        
        ruby_text = match.group(1)
        base_text = match.group(2)
        
        # 记录注音对应的分句索引
        ruby_map[part_index] = ruby_text
        
        current_pos = match.end()
    
    # 清理文本：把 @r注音@文本@ 替换为 文本
    clean_text = re.sub(pattern, r'\2', text)
    
    return clean_text, ruby_map


def pack_json_to_ss(ss_path, json_data, verbose=False):
    """将JSON数据封回SS文件"""
    try:
        ss_file = SSFile(ss_path)
        
        modified_count = 0
        ruby_count = 0
        name_count = 0
        
        for entry in json_data.get('entries', []):
            line = entry.get('line')
            text = entry.get('text', '')
            parts = entry.get('parts', [])
            name = entry.get('name', '')
            name_str_id = entry.get('name_str_id')
            
            # 处理name封回
            if name_str_id is not None and 0 <= name_str_id < ss_file.str_idx_cnt:
                old_name = ss_file.get_string(name_str_id)
                if name != old_name:
                    ss_file.set_string(name_str_id, name)
                    name_count += 1
                    if verbose:
                        print(f"    [名称 {name_str_id}] {old_name} -> {name if name else '(空)'}")
            
            if not parts:
                continue
            
            # 拆分合并的文本
            split_parts = split_merged_text(text, parts)
            
            for part in split_parts:
                str_id = part['str_id']
                new_text = part['text']
                ruby = part['ruby']
                ruby_str_id = part.get('ruby_str_id')
                
                # 更新主文本
                if 0 <= str_id < ss_file.str_idx_cnt:
                    old_text = ss_file.get_string(str_id)
                    if new_text != old_text:
                        ss_file.set_string(str_id, new_text)
                        modified_count += 1
                        if verbose:
                            print(f"    [{str_id}] {old_text[:20]}... -> {new_text[:20] if new_text else '(空)'}...")
                
                # 处理注音
                if ruby_str_id is not None and 0 <= ruby_str_id < ss_file.str_idx_cnt:
                    old_ruby = ss_file.get_string(ruby_str_id)
                    # 如果有新注音，更新注音
                    if ruby:
                        if ruby != old_ruby:
                            ss_file.set_string(ruby_str_id, ruby)
                            ruby_count += 1
                            if verbose:
                                print(f"    [注音 {ruby_str_id}] {old_ruby} -> {ruby}")
                    else:
                        # 没有注音了，清空原来的注音文本
                        if old_ruby:
                            ss_file.set_string(ruby_str_id, '')
                            ruby_count += 1
                            if verbose:
                                print(f"    [注音 {ruby_str_id}] {old_ruby} -> (删除)")
        
        return ss_file, modified_count, ruby_count, name_count
        
    except Exception as e:
        raise Exception(f"处理SS文件失败: {str(e)}")


def parse_txt_raw_file(txt_path):
    """
    解析全量文本TXT文件
    每行一个字符串，按索引顺序
    返回: [字符串列表]
    """
    strings = []
    
    # 尝试多种编码
    encodings = ['utf-16-le', 'utf-16', 'utf-8-sig', 'utf-8']
    content = None
    
    for enc in encodings:
        try:
            with open(txt_path, 'r', encoding=enc) as f:
                content = f.read()
                break
        except:
            continue
    
    if content is None:
        raise Exception(f"无法读取文件: {txt_path}")
    
    # 移除BOM
    if content.startswith('\ufeff'):
        content = content[1:]
    
    for line in content.split('\n'):
        # 反转义换行符
        text = line.replace('\\r\\n', '\r\n').replace('\\n', '\n').replace('\\r', '\r')
        strings.append(text)
    
    # 移除最后一个空元素（如果文件以换行结尾）
    if strings and strings[-1] == '':
        strings.pop()
    
    return strings


def pack_txt_raw_to_ss(ss_path, txt_strings, verbose=False):
    """
    将全量TXT中的字符串封回到SS文件
    
    参数:
        ss_path: 原始SS文件路径
        txt_strings: 字符串列表
        verbose: 是否详细输出
    
    返回:
        (ss_file, modified_count)
    """
    ss_file = SSFile(ss_path)
    modified_count = 0
    
    for i, new_text in enumerate(txt_strings):
        if i >= ss_file.str_idx_cnt:
            break
        
        old_text = ss_file.get_string(i)
        if new_text != old_text:
            ss_file.set_string(i, new_text)
            modified_count += 1
            if verbose:
                old_preview = old_text[:30] + '...' if len(old_text) > 30 else old_text
                new_preview = new_text[:30] + '...' if len(new_text) > 30 else new_text
                print(f"    [{i}] {old_preview} -> {new_preview}")
    
    return ss_file, modified_count


def parse_txt_trans_file(txt_path):
    """
    解析翻译文本TXT文件
    只解析 ◆编号◆内容 格式的行
    
    返回: {idx: 文本内容}
    """
    translations = {}
    
    # 尝试多种编码
    encodings = ['utf-16-le', 'utf-16', 'utf-8-sig', 'utf-8']
    content = None
    
    for enc in encodings:
        try:
            with open(txt_path, 'r', encoding=enc) as f:
                content = f.read()
                break
        except:
            continue
    
    if content is None:
        raise Exception(f"无法读取文件: {txt_path}")
    
    # 移除BOM
    if content.startswith('\ufeff'):
        content = content[1:]
    
    for line in content.split('\n'):
        line = line.strip()
        # 解析 ◆编号◆内容 格式
        if line.startswith('◆'):
            # 查找第二个◆
            second_marker = line.find('◆', 1)
            if second_marker > 1:
                try:
                    idx = int(line[1:second_marker])
                    text = line[second_marker + 1:]
                    translations[idx] = text
                except ValueError:
                    continue
    
    return translations


def merge_txt_trans_to_json(txt_path, json_path, out_path, verbose=False):
    """
    将翻译TXT合并到纯净JSON
    只更新 ◆ 行的内容
    
    参数:
        txt_path: 翻译TXT文件路径
        json_path: 原始纯净JSON文件路径
        out_path: 输出JSON文件路径
        verbose: 是否详细输出
    
    返回:
        modified_count
    """
    # 解析翻译TXT
    translations = parse_txt_trans_file(txt_path)
    
    # 读取原始JSON
    with open(json_path, 'r', encoding='utf-8') as jf:
        data = json.load(jf)
    
    # 确保是纯净格式（列表）
    if not isinstance(data, list):
        raise Exception("不支持的JSON格式，需要纯净格式 [{name, message}, ...]")
    
    modified_count = 0
    idx = 1
    
    for entry in data:
        name = entry.get('name', '')
        message = entry.get('message', '')
        
        # 更新name（如果有）
        if name:
            if idx in translations:
                new_name = translations[idx]
                if new_name != name:
                    entry['name'] = new_name
                    modified_count += 1
                    if verbose:
                        print(f"    [名称 {idx}] {name} -> {new_name}")
            idx += 1
        
        # 更新message
        if message:
            if idx in translations:
                new_message = translations[idx]
                if new_message != message:
                    entry['message'] = new_message
                    modified_count += 1
                    if verbose:
                        print(f"    [文本 {idx}] {message[:20]}... -> {new_message[:20]}...")
            idx += 1
    
    # 保存JSON
    with open(out_path, 'w', encoding='utf-8') as jf:
        json.dump(data, jf, ensure_ascii=False, indent=2)
    
    return modified_count


def pack_txt_trans_to_ss(ss_path, txt_path, verbose=False):
    """
    将翻译TXT直接封回到SS文件
    先从SS提取纯净JSON，合并翻译，再封回
    
    参数:
        ss_path: 原始SS文件路径
        txt_path: 翻译TXT文件路径
        verbose: 是否详细输出
    
    返回:
        (ss_file, modified_count, ruby_count, name_count)
    """
    # 解析翻译TXT
    translations = parse_txt_trans_file(txt_path)
    
    # 从SS提取原始结构
    original_entries = extract_entries_from_ss(ss_path)
    if not original_entries:
        raise Exception("无法从SS文件提取结构信息")
    
    # 构建纯净格式数据并更新
    import ssTextExtractor
    pure_data = []
    idx = 1
    
    for entry in original_entries:
        name = entry.get('name', '')
        text = entry.get('text', '')
        
        pure_entry = {}
        
        # 处理name
        if name:
            if idx in translations:
                pure_entry['name'] = translations[idx]
            else:
                pure_entry['name'] = name
            idx += 1
        
        # 处理message
        if text:
            if idx in translations:
                pure_entry['message'] = translations[idx]
            else:
                pure_entry['message'] = text
            idx += 1
        
        pure_data.append(pure_entry)
    
    # 合并并封回
    merged_data = merge_pure_with_original(pure_data, original_entries)
    return pack_json_to_ss(ss_path, merged_data, verbose)


def main(argv):
    # 解析选项
    verbose = '-v' in argv
    if verbose:
        argv.remove('-v')
    
    if len(argv) < 3 or argv[1] == '' or argv[2] == '':
        print("SS文本封回脚本 - 将JSON/ASM/TXT文本封回到.ss文件")
        print("")
        print("用法: " + os.path.basename(argv[0]) + " <Scene\\> <DATA\\> [Output\\] [options]")
        print("")
        print("参数:")
        print("  Scene\\  : 原始SS文件目录")
        print("  DATA\\   : JSON/ASM/TXT文本文件目录")
        print("  Output\\ : 输出目录（可选，默认为Scene_packed\\")
        print("")
        print("选项:")
        print("  -v: 详细输出模式")
        print("")
        print("支持的格式与对应提取模式:")
        print("  JSON完整格式 <- ssTextExtractor (默认模式)")
        print("    格式: {file, entries: [{line, name, name_str_id, text, parts}]}")
        print("  JSON纯净格式 <- ssTextExtractor -p")
        print("    格式: [{name, message}, ...]")
        print("  ASM格式 <- ssTextExtractor -d")
        print("    修改STRING TABLE部分的@STR[N]=...行")
        print("  全量TXT <- ssTextExtractor -t")
        print("    每行一个字符串，按索引顺序")
        print("  翻译TXT <- ssTextExtractor -T")
        print("    只导回◆编号◆内容 行")
        print("")
        print("注音格式: @r注音@被注音文本@")
        return False
    
    # 设置路径
    in_folder = argv[1].rstrip('\\') + '\\'
    data_folder = argv[2].rstrip('\\') + '\\'
    
    if len(argv) >= 4 and argv[3] != '':
        out_folder = argv[3].rstrip('\\') + '\\'
    else:
        out_folder = in_folder.rstrip('\\') + '_packed\\'
    
    if not os.path.exists(out_folder):
        os.makedirs(out_folder)
    
    # 查找所有支持的文件格式
    json_files = glob.glob(data_folder + "*.json")
    asm_files = glob.glob(data_folder + "*.asm")
    txt_files = glob.glob(data_folder + "*.txt")
    
    if not json_files and not asm_files and not txt_files:
        print(f"错误: 在 {data_folder} 中未找到.json/.asm/.txt文件")
        return False
    
    success_count = 0
    error_count = 0
    
    # 处理JSON文件
    if json_files:
        print(f"找到 {len(json_files)} 个JSON文件")
        
        for json_path in json_files:
            print(f"处理: {json_path}")
            
            try:
                # 读取JSON
                with open(json_path, 'r', encoding='utf-8') as jf:
                    json_data = json.load(jf)
                
                # 检测JSON格式
                json_format = detect_json_format(json_data)
                
                # 确定对应的SS文件
                if json_format == 'full':
                    ss_name = json_data.get('file', '')
                else:
                    ss_name = ''
                
                if not ss_name:
                    # 从JSON文件名推断
                    ss_name = os.path.basename(json_path).replace('.json', '')
                    if not ss_name.endswith('.ss'):
                        ss_name = ss_name + '.ss'
                
                ss_path = in_folder + ss_name
                ss_path = ss_path.replace('.ss.ss', '.ss')
                
                if not os.path.exists(ss_path):
                    print(f"  警告: 找不到源文件 {ss_path}")
                    error_count += 1
                    continue
                
                out_path = out_folder + ss_name.replace('.ss.ss', '.ss')

                # 【核心修复】如果JSON为空，直接复制原始文件
                if not json_data or json_format == 'empty':
                    print(f"  提示: JSON为空，直接复制原始文件")
                    shutil.copy(ss_path, out_path)
                    print(f"  保存(复制): {out_path}")
                    success_count += 1
                    continue

                # 处理纯净格式: 需要先提取原始结构
                if json_format == 'pure':
                    print(f"  检测到纯净格式JSON，从SS文件提取原始结构...")
                    original_entries = extract_entries_from_ss(ss_path)
                    
                    # 【核心修复】如果无法提取结构（通常因为无文本），直接复制原始文件
                    if not original_entries:
                        print(f"  提示: 原始文件无文本结构，直接复制原始文件")
                        shutil.copy(ss_path, out_path)
                        print(f"  保存(复制): {out_path}")
                        success_count += 1
                        continue
                        
                    json_data = merge_pure_with_original(json_data, original_entries)
                
                # 封回数据
                ss_file, modified, ruby_modified, name_modified = pack_json_to_ss(ss_path, json_data, verbose)
                
                # 保存
                ss_file.write(out_path)
                
                print(f"  保存: {out_path}")
                print(f"  修改: {modified} 个文本, {name_modified} 个名称, {ruby_modified} 个注音")
                
                success_count += 1
                
            except json.JSONDecodeError as e:
                print(f"  错误: JSON解析失败 - {str(e)}")
                error_count += 1
            except Exception as e:
                print(f"  错误: {str(e)}")
                # 出现其他错误时也尝试直接复制文件，保底
                try:
                    out_path = out_folder + os.path.basename(ss_path)
                    shutil.copy(ss_path, out_path)
                    print(f"  已执行保底复制")
                except:
                    pass
                error_count += 1
    
    # 处理ASM文件
    if asm_files:
        print(f"找到 {len(asm_files)} 个ASM文件")
        
        for asm_path in asm_files:
            print(f"处理: {asm_path}")
            
            try:
                # 解析ASM文件
                asm_strings = parse_asm_file(asm_path)
                
                if not asm_strings:
                    print(f"  跳过: 没有找到可封回的字符串")
                    continue
                
                # 从ASM文件名推断对应的SS文件
                ss_name = os.path.basename(asm_path).replace('.asm', '')
                if not ss_name.endswith('.ss'):
                    ss_name = ss_name + '.ss'
                
                ss_path = in_folder + ss_name
                ss_path = ss_path.replace('.ss.ss', '.ss')
                
                if not os.path.exists(ss_path):
                    print(f"  警告: 找不到源文件 {ss_path}")
                    error_count += 1
                    continue
                
                # 封回数据
                ss_file, modified = pack_asm_to_ss(ss_path, asm_strings, verbose)
                
                # 保存
                out_path = out_folder + ss_name.replace('.ss.ss', '.ss')
                ss_file.write(out_path)
                
                print(f"  保存: {out_path}")
                print(f"  修改: {modified} 个字符串 (共解析 {len(asm_strings)} 个)")
                
                success_count += 1
                
            except Exception as e:
                print(f"  错误: {str(e)}")
                error_count += 1
    
    # 处理TXT文件
    if txt_files:
        print(f"找到 {len(txt_files)} 个TXT文件")
        
        for txt_path in txt_files:
            print(f"处理: {txt_path}")
            
            try:
                # 从TXT文件名推断对应的SS文件
                ss_name = os.path.basename(txt_path).replace('.txt', '')
                if not ss_name.endswith('.ss'):
                    ss_name = ss_name + '.ss'
                
                ss_path = in_folder + ss_name
                ss_path = ss_path.replace('.ss.ss', '.ss')
                
                if not os.path.exists(ss_path):
                    print(f"  警告: 找不到源文件 {ss_path}")
                    error_count += 1
                    continue
                
                # 检测TXT格式
                with open(txt_path, 'rb') as f:
                    content = f.read(500)
                try:
                    text_sample = content.decode('utf-16-le', errors='ignore')
                except:
                    text_sample = content.decode('utf-8', errors='ignore')
                
                if '◆' in text_sample:
                    # 翻译模式 TXT
                    print(f"  检测到翻译TXT格式")
                    ss_file, modified, ruby_modified, name_modified = pack_txt_trans_to_ss(ss_path, txt_path, verbose)
                    
                    out_path = out_folder + ss_name.replace('.ss.ss', '.ss')
                    ss_file.write(out_path)
                    
                    print(f"  保存: {out_path}")
                    print(f"  修改: {modified} 个文本, {name_modified} 个名称, {ruby_modified} 个注音")
                else:
                    # 全量模式 TXT
                    print(f"  检测到全量TXT格式")
                    txt_strings = parse_txt_raw_file(txt_path)
                    ss_file, modified = pack_txt_raw_to_ss(ss_path, txt_strings, verbose)
                    
                    out_path = out_folder + ss_name.replace('.ss.ss', '.ss')
                    ss_file.write(out_path)
                    
                    print(f"  保存: {out_path}")
                    print(f"  修改: {modified} 个字符串 (共 {len(txt_strings)} 个)")
                
                success_count += 1
                
            except Exception as e:
                print(f"  错误: {str(e)}")
                import traceback
                traceback.print_exc()
                error_count += 1
    
    print("")
    print(f"完成！成功: {success_count}, 失败: {error_count}")
    return error_count == 0


if __name__ == "__main__":
    main(sys.argv)