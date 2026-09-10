# SiglusEngine

- 引擎：SiglusEngine
- 测试游戏：《anemoi 体验版》、《虹彩都市》、`gachihen`

## 文件

| 文件 | 用途 |
| --- | --- |
| `script-toolkit/GUI.py` | Gameexe、Scene/SS 脚本处理的图形界面。 |
| `script-toolkit/siglus_decrypt_unpack.py` | 解密 `Gameexe.dat`，并解包或重建 `Scene.pck`。 |
| `script-toolkit/ssTextExtractor.py` | 从 Scene/SS 脚本批量提取文本。 |
| `script-toolkit/ssTextPacker.py` | 将翻译文本回填并重建 Scene/SS 脚本。 |
| `script-toolkit/Decryption.py` | Siglus 数据解密与密钥处理模块。 |
| `script-toolkit/Decryption.cpp` | 解密算法的 C++ 实现与参考代码。 |
| `script-toolkit/KeyList.txt` | 多个游戏使用的 Siglus 密钥表。 |
| `script-toolkit/siglus_key.txt` | 工具读取的脚本解密密钥。 |
| `script-toolkit/SiglusKey.txt` | 备用格式的 Siglus 密钥记录。 |
| `g00-toolkit/` | G00 Type 0/1/2/3 图像与 PNG 的双向转换工具。 |
