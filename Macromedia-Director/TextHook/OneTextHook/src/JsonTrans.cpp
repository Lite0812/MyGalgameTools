#include "JsonTrans.h"

#include <Windows.h>

#include <algorithm>
#include <cstdio>
#include <cwctype>
#include <sstream>

namespace OneTextHook
{
    namespace
    {
        bool ReadAllBytes(const std::wstring& path, std::string& out)
        {
            FILE* fp = nullptr;
            if (_wfopen_s(&fp, path.c_str(), L"rb") != 0 || !fp)
            {
                return false;
            }
            fseek(fp, 0, SEEK_END);
            long size = ftell(fp);
            fseek(fp, 0, SEEK_SET);
            if (size < 0)
            {
                fclose(fp);
                return false;
            }
            out.assign(static_cast<size_t>(size), '\0');
            if (size > 0)
            {
                fread(&out[0], 1, static_cast<size_t>(size), fp);
            }
            fclose(fp);
            if (out.size() >= 3 &&
                static_cast<unsigned char>(out[0]) == 0xEF &&
                static_cast<unsigned char>(out[1]) == 0xBB &&
                static_cast<unsigned char>(out[2]) == 0xBF)
            {
                out.erase(0, 3);
            }
            return true;
        }

        bool WriteAllBytes(const std::wstring& path, const std::string& data)
        {
            FILE* fp = nullptr;
            if (_wfopen_s(&fp, path.c_str(), L"wb") != 0 || !fp)
            {
                return false;
            }
            const unsigned char bom[3] = { 0xEF, 0xBB, 0xBF };
            fwrite(bom, 1, sizeof(bom), fp);
            if (!data.empty())
            {
                fwrite(data.data(), 1, data.size(), fp);
            }
            fclose(fp);
            return true;
        }

        void SkipWs(const std::wstring& s, size_t& pos)
        {
            while (pos < s.size() && iswspace(s[pos]))
            {
                ++pos;
            }
        }

        bool ParseJsonString(const std::wstring& s, size_t& pos, std::wstring& out)
        {
            SkipWs(s, pos);
            if (pos >= s.size() || s[pos] != L'"')
            {
                return false;
            }
            ++pos;
            out.clear();
            while (pos < s.size())
            {
                wchar_t ch = s[pos++];
                if (ch == L'"')
                {
                    return true;
                }
                if (ch != L'\\')
                {
                    out.push_back(ch);
                    continue;
                }
                if (pos >= s.size())
                {
                    return false;
                }
                wchar_t esc = s[pos++];
                switch (esc)
                {
                case L'"': out.push_back(L'"'); break;
                case L'\\': out.push_back(L'\\'); break;
                case L'/': out.push_back(L'/'); break;
                case L'b': out.push_back(L'\b'); break;
                case L'f': out.push_back(L'\f'); break;
                case L'n': out.push_back(L'\n'); break;
                case L'r': out.push_back(L'\r'); break;
                case L't': out.push_back(L'\t'); break;
                case L'u':
                {
                    if (pos + 4 > s.size())
                    {
                        return false;
                    }
                    unsigned value = 0;
                    for (int i = 0; i < 4; ++i)
                    {
                        wchar_t h = s[pos++];
                        value <<= 4;
                        if (h >= L'0' && h <= L'9') value += h - L'0';
                        else if (h >= L'a' && h <= L'f') value += h - L'a' + 10;
                        else if (h >= L'A' && h <= L'F') value += h - L'A' + 10;
                        else return false;
                    }
                    out.push_back(static_cast<wchar_t>(value));
                    break;
                }
                default:
                    out.push_back(esc);
                    break;
                }
            }
            return false;
        }

        bool ParseJsonInt(const std::wstring& s, size_t& pos, int& out)
        {
            SkipWs(s, pos);
            bool neg = false;
            if (pos < s.size() && s[pos] == L'-')
            {
                neg = true;
                ++pos;
            }
            int value = 0;
            bool any = false;
            while (pos < s.size() && s[pos] >= L'0' && s[pos] <= L'9')
            {
                any = true;
                value = value * 10 + static_cast<int>(s[pos] - L'0');
                ++pos;
            }
            if (!any)
            {
                return false;
            }
            out = neg ? -value : value;
            return true;
        }

        void SkipJsonValue(const std::wstring& s, size_t& pos)
        {
            SkipWs(s, pos);
            if (pos >= s.size()) return;
            if (s[pos] == L'"')
            {
                std::wstring dummy;
                ParseJsonString(s, pos, dummy);
                return;
            }
            if (s[pos] == L'{' || s[pos] == L'[')
            {
                wchar_t open = s[pos++];
                wchar_t close = open == L'{' ? L'}' : L']';
                int depth = 1;
                while (pos < s.size() && depth > 0)
                {
                    if (s[pos] == L'"')
                    {
                        std::wstring dummy;
                        ParseJsonString(s, pos, dummy);
                    }
                    else if (s[pos] == open)
                    {
                        ++depth;
                        ++pos;
                    }
                    else if (s[pos] == close)
                    {
                        --depth;
                        ++pos;
                    }
                    else
                    {
                        ++pos;
                    }
                }
                return;
            }
            while (pos < s.size() && s[pos] != L',' && s[pos] != L'}' && s[pos] != L']')
            {
                ++pos;
            }
        }

        std::vector<TransEntry> ParseEntries(const std::wstring& json)
        {
            std::vector<TransEntry> entries;
            size_t pos = 0;
            SkipWs(json, pos);
            if (pos >= json.size() || json[pos] != L'[')
            {
                return entries;
            }
            ++pos;
            while (pos < json.size())
            {
                SkipWs(json, pos);
                if (pos < json.size() && json[pos] == L']')
                {
                    break;
                }
                if (pos >= json.size() || json[pos] != L'{')
                {
                    ++pos;
                    continue;
                }
                ++pos;
                TransEntry entry;
                while (pos < json.size())
                {
                    SkipWs(json, pos);
                    if (pos < json.size() && json[pos] == L'}')
                    {
                        ++pos;
                        break;
                    }
                    std::wstring key;
                    if (!ParseJsonString(json, pos, key))
                    {
                        SkipJsonValue(json, pos);
                        continue;
                    }
                    SkipWs(json, pos);
                    if (pos < json.size() && json[pos] == L':')
                    {
                        ++pos;
                    }
                    if (key == L"id")
                    {
                        ParseJsonString(json, pos, entry.id);
                    }
                    else if (key == L"original" || key == L"src_jp")
                    {
                        ParseJsonString(json, pos, entry.original);
                    }
                    else if (key == L"translation" || key == L"message")
                    {
                        ParseJsonString(json, pos, entry.translation);
                    }
                    else if (key == L"count")
                    {
                        ParseJsonInt(json, pos, entry.count);
                    }
                    else
                    {
                        SkipJsonValue(json, pos);
                    }
                    SkipWs(json, pos);
                    if (pos < json.size() && json[pos] == L',')
                    {
                        ++pos;
                    }
                }
                if (!entry.original.empty())
                {
                    entries.push_back(entry);
                }
                SkipWs(json, pos);
                if (pos < json.size() && json[pos] == L',')
                {
                    ++pos;
                }
            }
            return entries;
        }

        std::string EscapeJsonUtf8(const std::wstring& text)
        {
            std::string out;
            for (wchar_t ch : text)
            {
                switch (ch)
                {
                case L'"': out += "\\\""; break;
                case L'\\': out += "\\\\"; break;
                case L'\b': out += "\\b"; break;
                case L'\f': out += "\\f"; break;
                case L'\n': out += "\\n"; break;
                case L'\r': out += "\\r"; break;
                case L'\t': out += "\\t"; break;
                default:
                    if (ch < 0x20)
                    {
                        char buf[8];
                        sprintf_s(buf, "\\u%04x", static_cast<unsigned>(ch));
                        out += buf;
                    }
                    else
                    {
                        out += WideToUtf8(std::wstring(1, ch));
                    }
                    break;
                }
            }
            return out;
        }

        int ParseIdNumber(const std::wstring& id)
        {
            if (id.size() < 2 || id[0] != L'T')
            {
                return 0;
            }
            int n = 0;
            for (size_t i = 1; i < id.size(); ++i)
            {
                if (id[i] < L'0' || id[i] > L'9')
                {
                    return 0;
                }
                n = n * 10 + static_cast<int>(id[i] - L'0');
            }
            return n;
        }
    }

    std::string WideToUtf8(const std::wstring& text)
    {
        if (text.empty()) return std::string();
        int len = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
        if (len <= 0) return std::string();
        std::string out(static_cast<size_t>(len), '\0');
        WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), &out[0], len, nullptr, nullptr);
        return out;
    }

    std::wstring Utf8ToWide(const std::string& text)
    {
        if (text.empty()) return std::wstring();
        int len = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
        if (len <= 0) return std::wstring();
        std::wstring out(static_cast<size_t>(len), L'\0');
        MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), &out[0], len);
        return out;
    }

    std::wstring AnsiToWide(const char* text, int length, UINT codePage)
    {
        if (!text) return std::wstring();
        int len = length;
        if (len < 0)
        {
            len = static_cast<int>(strlen(text));
        }
        if (len <= 0 || len > 8192) return std::wstring();
        int wideLen = MultiByteToWideChar(codePage, MB_ERR_INVALID_CHARS, text, len, nullptr, 0);
        if (wideLen <= 0)
        {
            wideLen = MultiByteToWideChar(codePage, 0, text, len, nullptr, 0);
        }
        if (wideLen <= 0) return std::wstring();
        std::wstring out(static_cast<size_t>(wideLen), L'\0');
        MultiByteToWideChar(codePage, 0, text, len, &out[0], wideLen);
        return out;
    }

    std::string WideToAnsi(const std::wstring& text, UINT codePage)
    {
        if (text.empty()) return std::string();
        int len = WideCharToMultiByte(codePage, 0, text.c_str(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
        if (len <= 0) return std::string();
        std::string out(static_cast<size_t>(len), '\0');
        WideCharToMultiByte(codePage, 0, text.c_str(), static_cast<int>(text.size()), &out[0], len, nullptr, nullptr);
        return out;
    }

    std::wstring NormalizeText(const std::wstring& text)
    {
        std::wstring out;
        out.reserve(text.size());
        bool lastSpace = false;
        for (wchar_t ch : text)
        {
            if (ch == L'\r' || ch == L'\n' || ch == L'\t' || ch == L' ')
            {
                if (!lastSpace)
                {
                    out.push_back(L' ');
                    lastSpace = true;
                }
            }
            else
            {
                out.push_back(ch);
                lastSpace = false;
            }
        }
        while (!out.empty() && iswspace(out.front())) out.erase(out.begin());
        while (!out.empty() && iswspace(out.back())) out.pop_back();
        return out;
    }

    bool LooksLikeUsefulText(const std::wstring& text, bool keepAscii, bool hasTranslation)
    {
        if (text.empty()) return false;
        bool asciiOnly = true;
        bool hasVisible = false;
        for (wchar_t ch : text)
        {
            if (ch < 0x20 || ch == 0x7f) return false;
            if (!iswspace(ch)) hasVisible = true;
            if (ch >= 0x80) asciiOnly = false;
        }
        if (!hasVisible) return false;
        if (!keepAscii && asciiOnly && !hasTranslation) return false;
        if (text.size() == 1 && !hasTranslation)
        {
            wchar_t ch = text[0];
            if (iswpunct(ch) || ch == L'。' || ch == L'、' || ch == L'「' || ch == L'」')
            {
                return false;
            }
        }
        return true;
    }

    bool TranslationStore::LoadTranslations(const std::wstring& path)
    {
        std::string bytes;
        if (!ReadAllBytes(path, bytes))
        {
            return false;
        }
        std::vector<TransEntry> entries = ParseEntries(Utf8ToWide(bytes));
        for (const auto& entry : entries)
        {
            if (!entry.original.empty() && !entry.translation.empty() && entry.translation != entry.original)
            {
                translations_[entry.original] = entry.translation;
            }
        }
        return true;
    }

    bool TranslationStore::LoadExtract(const std::wstring& path)
    {
        std::string bytes;
        if (!ReadAllBytes(path, bytes))
        {
            return false;
        }
        std::vector<TransEntry> entries = ParseEntries(Utf8ToWide(bytes));
        for (const auto& entry : entries)
        {
            AddExtractEntry(entry);
        }
        return true;
    }

    bool TranslationStore::SaveExtractAtomic(const std::wstring& path) const
    {
        std::ostringstream oss;
        oss << "[\r\n";
        for (size_t i = 0; i < extractEntries_.size(); ++i)
        {
            const auto& e = extractEntries_[i];
            oss << "  {\r\n";
            oss << "    \"src_jp\": \"" << EscapeJsonUtf8(e.original) << "\",\r\n";
            oss << "    \"message\": \"" << EscapeJsonUtf8(e.translation.empty() ? e.original : e.translation) << "\"\r\n";
            oss << "  }" << (i + 1 == extractEntries_.size() ? "\r\n" : ",\r\n");
        }
        oss << "]\r\n";

        std::wstring tmp = path + L".tmp";
        if (!WriteAllBytes(tmp, oss.str()))
        {
            return false;
        }
        return MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != FALSE;
    }

    bool TranslationStore::FindTranslation(const std::wstring& original, std::wstring& translation) const
    {
        auto it = translations_.find(original);
        if (it == translations_.end()) return false;
        translation = it->second;
        return true;
    }

    bool TranslationStore::RecordSeen(const std::wstring& original)
    {
        auto it = extractIndex_.find(original);
        if (it != extractIndex_.end())
        {
            ++extractEntries_[it->second].count;
            return true;
        }
        TransEntry entry;
        entry.id = MakeId(nextId_++);
        entry.original = original;
        entry.translation = entry.original;
        entry.count = 1;
        AddExtractEntry(entry);
        return true;
    }

    bool TranslationStore::HasOriginal(const std::wstring& original) const
    {
        return translations_.find(original) != translations_.end() || extractIndex_.find(original) != extractIndex_.end();
    }

    void TranslationStore::AddExtractEntry(const TransEntry& entry)
    {
        if (entry.original.empty()) return;
        auto it = extractIndex_.find(entry.original);
        if (it != extractIndex_.end())
        {
            extractEntries_[it->second].count = (extractEntries_[it->second].count > entry.count)
                ? extractEntries_[it->second].count
                : entry.count;
            return;
        }
        extractIndex_[entry.original] = extractEntries_.size();
        extractEntries_.push_back(entry);
        int idNum = ParseIdNumber(entry.id);
        if (idNum >= nextId_) nextId_ = idNum + 1;
    }

    std::wstring TranslationStore::MakeId(int index)
    {
        wchar_t buf[32];
        swprintf_s(buf, L"T%06d", index);
        return buf;
    }
}
