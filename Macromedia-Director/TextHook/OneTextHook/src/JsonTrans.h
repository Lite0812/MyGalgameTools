#pragma once

#include "TextHook.h"

#include <string>
#include <unordered_map>
#include <vector>

namespace OneTextHook
{
    struct TransEntry
    {
        std::wstring id;
        std::wstring original;
        std::wstring translation;
        int count = 0;
    };

    class TranslationStore
    {
    public:
        bool LoadTranslations(const std::wstring& path);
        bool LoadExtract(const std::wstring& path);
        bool SaveExtractAtomic(const std::wstring& path) const;

        bool FindTranslation(const std::wstring& original, std::wstring& translation) const;
        bool RecordSeen(const std::wstring& original);
        bool HasOriginal(const std::wstring& original) const;
        size_t TranslationCount() const { return translations_.size(); }
        size_t ExtractCount() const { return extractEntries_.size(); }

    private:
        std::unordered_map<std::wstring, std::wstring> translations_;
        std::vector<TransEntry> extractEntries_;
        std::unordered_map<std::wstring, size_t> extractIndex_;
        int nextId_ = 1;

        void AddExtractEntry(const TransEntry& entry);
        static std::wstring MakeId(int index);
    };

    std::string WideToUtf8(const std::wstring& text);
    std::wstring Utf8ToWide(const std::string& text);
    std::wstring AnsiToWide(const char* text, int length, UINT codePage);
    std::string WideToAnsi(const std::wstring& text, UINT codePage);
    std::wstring NormalizeText(const std::wstring& text);
    bool LooksLikeUsefulText(const std::wstring& text, bool keepAscii, bool hasTranslation);
}
