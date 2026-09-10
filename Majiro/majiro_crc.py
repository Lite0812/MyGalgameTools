import struct

class Crc:
    _crc32_table = None
    _crypt_key32 = None

    @staticmethod
    def _init_tables():
        if Crc._crc32_table is not None:
            return
        
        poly = 0xEDB88320
        table = []
        for i in range(256):
            value = i
            for _ in range(8):
                if value & 1:
                    value = (value >> 1) ^ poly
                else:
                    value >>= 1
            table.append(value)
        Crc._crc32_table = table
        
        # Generate CryptKey32
        key = bytearray()
        for val in table:
            key.extend(struct.pack('<I', val))
        Crc._crypt_key32 = key

    @staticmethod
    def crypt32(data: bytearray, key_offset=0):
        Crc._init_tables()
        key_len = len(Crc._crypt_key32)
        for i in range(len(data)):
            data[i] ^= Crc._crypt_key32[(key_offset + i) & 0x3FF]

    @staticmethod
    def hash32(data: bytes, init=0):
        Crc._init_tables()
        result = ~init & 0xFFFFFFFF
        for b in data:
            result = (result >> 8) ^ Crc._crc32_table[(result ^ b) & 0xFF]
        return ~result & 0xFFFFFFFF

    @staticmethod
    def hash32_str(s: str, encoding='shift_jis', init=0):
        return Crc.hash32(s.encode(encoding), init)
