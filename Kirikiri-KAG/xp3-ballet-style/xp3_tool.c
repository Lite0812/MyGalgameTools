#define _CRT_SECURE_NO_WARNINGS
#define _WIN32_WINNT 0x0600
#define WIN32_LEAN_AND_MEAN

#include <windows.h>

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <io.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <wchar.h>

#include "miniz/miniz.h"

#define META_FILENAME L".xp3meta.json"
#define FILTER_STREAM_LEN 61
#define FILTER_KEY_STREAM_LEN 31

static const unsigned char XP3_SIGNATURE[] = {
    'X', 'P', '3', '\r', '\n', ' ', '\n', 0x1A, 0x8B, 'g', 0x01,
};

typedef struct {
    uint64_t offset;
    uint64_t original_size;
    uint64_t archived_size;
    int compressed;
} XP3Segment;

typedef struct {
    wchar_t *name;
    uint32_t info_flags;
    uint64_t original_size;
    uint64_t archived_size;
    uint32_t adler32;
    int has_adler;
    XP3Segment *segments;
    size_t segment_count;
} XP3Entry;

typedef struct {
    HANDLE file;
    HANDLE mapping;
    unsigned char *view;
    size_t size;
} MappedFile;

typedef struct {
    wchar_t *path;
    uint64_t xp3_offset;
    uint64_t index_offset;
    int index_compressed;
    XP3Entry *entries;
    size_t entry_count;
    size_t entry_capacity;
    MappedFile mapped;
} XP3Archive;

typedef struct {
    unsigned char *data;
    size_t size;
    size_t capacity;
} ByteBuffer;

typedef struct {
    wchar_t *abs_path;
    wchar_t *rel_name;
} InputFile;

typedef struct {
    InputFile *items;
    size_t count;
    size_t capacity;
} InputFileList;

typedef struct {
    const char *label;
    size_t total;
    size_t current;
    int last_percent;
    int interactive;
    ULONGLONG last_tick;
} ProgressBar;

typedef struct {
    uint64_t total;
    unsigned char stream[FILTER_STREAM_LEN];
} ExtransContext;

static const uint64_t BUILTIN_EXTRANS_TOTAL = 0x8A9E3AAEED2BD8B4ULL;

static void close_mapped_file(MappedFile *mapped) {
    if (mapped->view != NULL) {
        UnmapViewOfFile(mapped->view);
    }
    if (mapped->mapping != NULL && mapped->mapping != INVALID_HANDLE_VALUE) {
        CloseHandle(mapped->mapping);
    }
    if (mapped->file != NULL && mapped->file != INVALID_HANDLE_VALUE) {
        CloseHandle(mapped->file);
    }
    memset(mapped, 0, sizeof(*mapped));
    mapped->file = INVALID_HANDLE_VALUE;
    mapped->mapping = INVALID_HANDLE_VALUE;
}

static int fail_message(const char *message) {
    fprintf(stderr, "error: %s\n", message);
    return 0;
}

static int fail_win32(const wchar_t *prefix, const wchar_t *path) {
    DWORD error = GetLastError();
    fwprintf(stderr, L"error: %ls: %ls (win32=%lu)\n", prefix, path, (unsigned long)error);
    return 0;
}

static void *checked_malloc(size_t size) {
    void *memory = malloc(size == 0 ? 1 : size);
    if (memory == NULL) {
        fprintf(stderr, "error: out of memory\n");
    }
    return memory;
}

static void *checked_realloc(void *memory, size_t size) {
    void *result = realloc(memory, size == 0 ? 1 : size);
    if (result == NULL) {
        fprintf(stderr, "error: out of memory\n");
    }
    return result;
}

static wchar_t *dup_wstring(const wchar_t *text) {
    size_t length = wcslen(text);
    wchar_t *copy = (wchar_t *)checked_malloc((length + 1) * sizeof(wchar_t));
    if (copy == NULL) {
        return NULL;
    }
    memcpy(copy, text, (length + 1) * sizeof(wchar_t));
    return copy;
}

static wchar_t *join_paths(const wchar_t *left, const wchar_t *right, wchar_t separator, int normalize_right) {
    size_t left_len = wcslen(left);
    size_t right_len = wcslen(right);
    size_t need_sep = (left_len > 0 && right_len > 0) ? 1 : 0;
    wchar_t *result = (wchar_t *)checked_malloc((left_len + right_len + need_sep + 1) * sizeof(wchar_t));
    size_t index;
    if (result == NULL) {
        return NULL;
    }

    memcpy(result, left, left_len * sizeof(wchar_t));
    index = left_len;
    if (need_sep) {
        result[index++] = separator;
    }
    for (size_t i = 0; i < right_len; ++i) {
        wchar_t ch = right[i];
        if (normalize_right && (ch == L'/' || ch == L'\\')) {
            ch = separator;
        }
        result[index++] = ch;
    }
    result[index] = 0;
    return result;
}

static wchar_t *path_without_extension(const wchar_t *path) {
    size_t length = wcslen(path);
    wchar_t *copy = dup_wstring(path);
    size_t slash_index = (size_t)-1;
    size_t dot_index = (size_t)-1;
    if (copy == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < length; ++i) {
        if (copy[i] == L'/' || copy[i] == L'\\') {
            slash_index = i;
            dot_index = (size_t)-1;
        } else if (copy[i] == L'.' && (slash_index == (size_t)-1 || i > slash_index + 0)) {
            dot_index = i;
        }
    }
    if (dot_index != (size_t)-1) {
        copy[dot_index] = 0;
    }
    return copy;
}

static wchar_t *path_with_extension(const wchar_t *path, const wchar_t *extension) {
    wchar_t *base = path_without_extension(path);
    wchar_t *result;
    size_t base_len;
    size_t ext_len;
    if (base == NULL) {
        return NULL;
    }
    base_len = wcslen(base);
    ext_len = wcslen(extension);
    result = (wchar_t *)checked_malloc((base_len + ext_len + 1) * sizeof(wchar_t));
    if (result == NULL) {
        free(base);
        return NULL;
    }
    memcpy(result, base, base_len * sizeof(wchar_t));
    memcpy(result + base_len, extension, (ext_len + 1) * sizeof(wchar_t));
    free(base);
    return result;
}

static int ensure_directory_recursive(const wchar_t *path) {
    wchar_t *copy = dup_wstring(path);
    size_t start = 0;
    size_t length;
    if (copy == NULL) {
        return 0;
    }
    length = wcslen(copy);
    if (length >= 2 && copy[1] == L':') {
        start = (length >= 3 && (copy[2] == L'/' || copy[2] == L'\\')) ? 3 : 2;
    } else if (length >= 2 && copy[0] == L'\\' && copy[1] == L'\\') {
        int separators = 0;
        for (size_t i = 2; i < length; ++i) {
            if (copy[i] == L'/' || copy[i] == L'\\') {
                separators += 1;
                if (separators == 2) {
                    start = i + 1;
                    break;
                }
            }
        }
    }
    for (size_t i = start; i < length; ++i) {
        if (copy[i] == L'/' || copy[i] == L'\\') {
            wchar_t saved = copy[i];
            copy[i] = 0;
            if (wcslen(copy) > 0 && !CreateDirectoryW(copy, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) {
                free(copy);
                return fail_win32(L"cannot create directory", path);
            }
            copy[i] = saved;
        }
    }
    if (!CreateDirectoryW(copy, NULL) && GetLastError() != ERROR_ALREADY_EXISTS) {
        free(copy);
        return fail_win32(L"cannot create directory", path);
    }
    free(copy);
    return 1;
}

static int ensure_parent_directory(const wchar_t *path) {
    wchar_t *copy = dup_wstring(path);
    wchar_t *slash;
    int ok = 1;
    if (copy == NULL) {
        return 0;
    }
    slash = wcsrchr(copy, L'\\');
    if (slash == NULL) {
        slash = wcsrchr(copy, L'/');
    }
    if (slash != NULL) {
        *slash = 0;
        if (copy[0] != 0) {
            ok = ensure_directory_recursive(copy);
        }
    }
    free(copy);
    return ok;
}

static int is_safe_internal_name(const wchar_t *name) {
    size_t length = wcslen(name);
    size_t segment_start = 0;
    if (length == 0) {
        return 0;
    }
    if (name[0] == L'/' || name[0] == L'\\') {
        return 0;
    }
    for (size_t i = 0; i <= length; ++i) {
        wchar_t ch = name[i];
        if (ch == L':') {
            return 0;
        }
        if (ch == 0 || ch == L'/' || ch == L'\\') {
            size_t segment_length = i - segment_start;
            if (segment_length == 0) {
                return 0;
            }
            if (segment_length == 1 && name[segment_start] == L'.') {
                return 0;
            }
            if (segment_length == 2 && name[segment_start] == L'.' && name[segment_start + 1] == L'.') {
                return 0;
            }
            segment_start = i + 1;
        }
    }
    return 1;
}

static wchar_t *build_output_path(const wchar_t *root, const wchar_t *internal_name) {
    wchar_t *result;
    size_t root_len;
    size_t name_len;
    size_t index;
    if (!is_safe_internal_name(internal_name)) {
        fail_message("unsafe archive path");
        return NULL;
    }
    root_len = wcslen(root);
    name_len = wcslen(internal_name);
    result = (wchar_t *)checked_malloc((root_len + name_len + 2) * sizeof(wchar_t));
    if (result == NULL) {
        return NULL;
    }
    memcpy(result, root, root_len * sizeof(wchar_t));
    index = root_len;
    if (root_len > 0 && root[root_len - 1] != L'\\' && root[root_len - 1] != L'/') {
        result[index++] = L'\\';
    }
    for (size_t i = 0; i < name_len; ++i) {
        wchar_t ch = internal_name[i];
        result[index++] = (ch == L'/') ? L'\\' : ch;
    }
    result[index] = 0;
    return result;
}

static int buffer_reserve(ByteBuffer *buffer, size_t needed) {
    unsigned char *memory;
    size_t capacity = buffer->capacity == 0 ? 256 : buffer->capacity;
    if (needed <= buffer->capacity) {
        return 1;
    }
    while (capacity < needed) {
        if (capacity > SIZE_MAX / 2) {
            capacity = needed;
            break;
        }
        capacity *= 2;
    }
    memory = (unsigned char *)checked_realloc(buffer->data, capacity);
    if (memory == NULL) {
        return 0;
    }
    buffer->data = memory;
    buffer->capacity = capacity;
    return 1;
}

static int buffer_append(ByteBuffer *buffer, const void *data, size_t size) {
    if (!buffer_reserve(buffer, buffer->size + size)) {
        return 0;
    }
    memcpy(buffer->data + buffer->size, data, size);
    buffer->size += size;
    return 1;
}

static int buffer_append_u16(ByteBuffer *buffer, uint16_t value) {
    unsigned char bytes[2];
    bytes[0] = (unsigned char)(value & 0xFF);
    bytes[1] = (unsigned char)((value >> 8) & 0xFF);
    return buffer_append(buffer, bytes, sizeof(bytes));
}

static int buffer_append_u32(ByteBuffer *buffer, uint32_t value) {
    unsigned char bytes[4];
    bytes[0] = (unsigned char)(value & 0xFF);
    bytes[1] = (unsigned char)((value >> 8) & 0xFF);
    bytes[2] = (unsigned char)((value >> 16) & 0xFF);
    bytes[3] = (unsigned char)((value >> 24) & 0xFF);
    return buffer_append(buffer, bytes, sizeof(bytes));
}

static int buffer_append_u64(ByteBuffer *buffer, uint64_t value) {
    unsigned char bytes[8];
    for (int i = 0; i < 8; ++i) {
        bytes[i] = (unsigned char)((value >> (8 * i)) & 0xFF);
    }
    return buffer_append(buffer, bytes, sizeof(bytes));
}

static void buffer_free(ByteBuffer *buffer) {
    free(buffer->data);
    buffer->data = NULL;
    buffer->size = 0;
    buffer->capacity = 0;
}

static uint16_t read_u16_le(const unsigned char *data) {
    return (uint16_t)(data[0] | ((uint16_t)data[1] << 8));
}

static uint32_t read_u32_le(const unsigned char *data) {
    return (uint32_t)data[0]
        | ((uint32_t)data[1] << 8)
        | ((uint32_t)data[2] << 16)
        | ((uint32_t)data[3] << 24);
}

static uint64_t read_u64_le(const unsigned char *data) {
    uint64_t value = 0;
    for (int i = 0; i < 8; ++i) {
        value |= ((uint64_t)data[i]) << (8 * i);
    }
    return value;
}

static uint32_t u32(uint32_t value) {
    return value & 0xFFFFFFFFU;
}

static uint32_t shrd32(uint32_t low, uint32_t high, int shift) {
    return u32((low >> shift) | ((high << (32 - shift)) & 0xFFFFFFFFU));
}

static void build_total_stream(uint64_t total, unsigned char out_stream[FILTER_STREAM_LEN]) {
    uint32_t low = (uint32_t)(total & 0xFFFFFFFFU);
    uint32_t high = (uint32_t)((total >> 32) & 0x1FFFFFFFU);
    for (size_t i = 0; i < FILTER_STREAM_LEN; ++i) {
        unsigned char byte = (unsigned char)(low & 0xFFU);
        out_stream[i] = byte;
        low = shrd32(low, high, 8);
        high = u32((high >> 8) | ((uint32_t)byte << 21));
    }
}

static void build_key_stream(uint32_t key, unsigned char out_stream[FILTER_KEY_STREAM_LEN]) {
    uint32_t state = key & 0x7FFFFFFFU;
    for (size_t i = 0; i < FILTER_KEY_STREAM_LEN; ++i) {
        out_stream[i] = (unsigned char)(state & 0xFFU);
        state = u32((state >> 8) | ((state & 0xFFU) << 23));
    }
}

static void build_extrans_context(ExtransContext *context) {
    context->total = BUILTIN_EXTRANS_TOTAL;
    build_total_stream(context->total, context->stream);
}

static void apply_extrans_filter(unsigned char *data, size_t size, uint32_t key, const ExtransContext *context) {
    unsigned char key_stream[FILTER_KEY_STREAM_LEN];
    build_key_stream(key, key_stream);
    for (size_t i = 0; i < size; ++i) {
        data[i] ^= key_stream[i % FILTER_KEY_STREAM_LEN];
        data[i] = (unsigned char)((data[i] + context->stream[i % FILTER_STREAM_LEN]) & 0xFFU);
    }
}

static void reverse_extrans_filter(unsigned char *data, size_t size, uint32_t key, const ExtransContext *context) {
    unsigned char key_stream[FILTER_KEY_STREAM_LEN];
    build_key_stream(key, key_stream);
    for (size_t i = 0; i < size; ++i) {
        data[i] = (unsigned char)((data[i] - context->stream[i % FILTER_STREAM_LEN]) & 0xFFU);
        data[i] ^= key_stream[i % FILTER_KEY_STREAM_LEN];
    }
}

static int stdout_is_interactive(void) {
    DWORD mode;
    HANDLE handle = GetStdHandle(STD_OUTPUT_HANDLE);
    if (handle == INVALID_HANDLE_VALUE || handle == NULL) {
        return 0;
    }
    return GetConsoleMode(handle, &mode) ? 1 : 0;
}

static void progress_init(ProgressBar *progress, const char *label, size_t total) {
    progress->label = label;
    progress->total = total;
    progress->current = 0;
    progress->last_percent = -1;
    progress->interactive = stdout_is_interactive();
    progress->last_tick = 0;
}

static void progress_render(ProgressBar *progress, int final) {
    int percent = (progress->total == 0) ? 100 : (int)((progress->current * 100) / progress->total);
    if (progress->interactive) {
        int width = 28;
        int filled = (progress->total == 0) ? width : (int)((progress->current * width) / progress->total);
        printf("\r%s [", progress->label);
        for (int i = 0; i < width; ++i) {
            putchar(i < filled ? '#' : '-');
        }
         printf("] %" PRIu64 "/%" PRIu64 " %6.2f%%",
             (uint64_t)progress->current,
             (uint64_t)progress->total,
               progress->total == 0 ? 100.0 : ((double)progress->current * 100.0) / (double)progress->total);
        if (final) {
            putchar('\n');
        }
    } else {
         printf("[%s] %" PRIu64 "/%" PRIu64 " (%d%%)\n",
               progress->label,
             (uint64_t)progress->current,
             (uint64_t)progress->total,
               percent);
    }
    fflush(stdout);
    progress->last_percent = percent;
    progress->last_tick = GetTickCount64();
}

static void progress_advance(ProgressBar *progress, size_t step) {
    int percent;
    ULONGLONG now;
    progress->current += step;
    if (progress->current > progress->total) {
        progress->current = progress->total;
    }
    percent = (progress->total == 0) ? 100 : (int)((progress->current * 100) / progress->total);
    now = GetTickCount64();
    if (progress->interactive) {
        if (progress->current == progress->total || percent != progress->last_percent || now - progress->last_tick >= 100) {
            progress_render(progress, progress->current == progress->total);
        }
    } else {
        if (progress->current == progress->total || percent >= progress->last_percent + 10) {
            progress_render(progress, progress->current == progress->total);
        }
    }
}

static void progress_finish(ProgressBar *progress) {
    progress->current = progress->total;
    progress_render(progress, 1);
}

static int open_mapped_file(const wchar_t *path, MappedFile *mapped) {
    LARGE_INTEGER size;
    memset(mapped, 0, sizeof(*mapped));
    mapped->file = INVALID_HANDLE_VALUE;
    mapped->mapping = INVALID_HANDLE_VALUE;

    mapped->file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (mapped->file == INVALID_HANDLE_VALUE) {
        return fail_win32(L"cannot open file", path);
    }
    if (!GetFileSizeEx(mapped->file, &size)) {
        close_mapped_file(mapped);
        return fail_win32(L"cannot get file size", path);
    }
    if (size.QuadPart < 0 || (uint64_t)size.QuadPart > (uint64_t)SIZE_MAX) {
        close_mapped_file(mapped);
        return fail_message("file too large for this build");
    }
    mapped->size = (size_t)size.QuadPart;
    mapped->mapping = CreateFileMappingW(mapped->file, NULL, PAGE_READONLY, 0, 0, NULL);
    if (mapped->mapping == NULL) {
        close_mapped_file(mapped);
        return fail_win32(L"cannot create file mapping", path);
    }
    mapped->view = (unsigned char *)MapViewOfFile(mapped->mapping, FILE_MAP_READ, 0, 0, 0);
    if (mapped->view == NULL) {
        close_mapped_file(mapped);
        return fail_win32(L"cannot map file", path);
    }
    return 1;
}

static void free_entry(XP3Entry *entry) {
    free(entry->name);
    free(entry->segments);
    memset(entry, 0, sizeof(*entry));
}

static void free_archive(XP3Archive *archive) {
    for (size_t i = 0; i < archive->entry_count; ++i) {
        free_entry(&archive->entries[i]);
    }
    free(archive->entries);
    free(archive->path);
    archive->entries = NULL;
    archive->entry_count = 0;
    archive->entry_capacity = 0;
    close_mapped_file(&archive->mapped);
}

static int archive_append_entry(XP3Archive *archive, XP3Entry *entry) {
    XP3Entry *items;
    size_t capacity = archive->entry_capacity == 0 ? 32 : archive->entry_capacity * 2;
    if (archive->entry_count == archive->entry_capacity) {
        items = (XP3Entry *)checked_realloc(archive->entries, capacity * sizeof(XP3Entry));
        if (items == NULL) {
            return 0;
        }
        archive->entries = items;
        archive->entry_capacity = capacity;
    }
    archive->entries[archive->entry_count++] = *entry;
    memset(entry, 0, sizeof(*entry));
    return 1;
}

static int parse_info_chunk(const unsigned char *payload, size_t size, XP3Entry *entry) {
    uint16_t name_units;
    size_t name_bytes;
    if (size < 22) {
        return fail_message("info chunk is too short");
    }
    entry->info_flags = read_u32_le(payload);
    entry->original_size = read_u64_le(payload + 4);
    entry->archived_size = read_u64_le(payload + 12);
    name_units = read_u16_le(payload + 20);
    name_bytes = (size_t)name_units * 2;
    if (22 + name_bytes > size) {
        return fail_message("truncated XP3 file name");
    }
    entry->name = (wchar_t *)checked_malloc(((size_t)name_units + 1) * sizeof(wchar_t));
    if (entry->name == NULL) {
        return 0;
    }
    for (size_t i = 0; i < (size_t)name_units; ++i) {
        entry->name[i] = (wchar_t)read_u16_le(payload + 22 + i * 2);
    }
    entry->name[name_units] = 0;
    return 1;
}

static int parse_segments_chunk(const unsigned char *payload, size_t size, XP3Entry *entry) {
    size_t count;
    if (size % 28 != 0) {
        return fail_message("segm chunk size is not aligned");
    }
    count = size / 28;
    entry->segments = (XP3Segment *)checked_malloc((count == 0 ? 1 : count) * sizeof(XP3Segment));
    if (entry->segments == NULL) {
        return 0;
    }
    entry->segment_count = count;
    for (size_t i = 0; i < count; ++i) {
        const unsigned char *item = payload + i * 28;
        uint32_t flags = read_u32_le(item);
        uint32_t compression_mode = flags & 0x7U;
        if (compression_mode != 0 && compression_mode != 1) {
            return fail_message("unsupported segment compression mode");
        }
        entry->segments[i].compressed = compression_mode ? 1 : 0;
        entry->segments[i].offset = read_u64_le(item + 4);
        entry->segments[i].original_size = read_u64_le(item + 12);
        entry->segments[i].archived_size = read_u64_le(item + 20);
    }
    return 1;
}

static int parse_file_record(const unsigned char *payload, size_t size, XP3Entry *entry) {
    const unsigned char *info_payload = NULL;
    const unsigned char *segm_payload = NULL;
    const unsigned char *adlr_payload = NULL;
    size_t info_size = 0;
    size_t segm_size = 0;
    size_t adlr_size = 0;
    size_t offset = 0;
    memset(entry, 0, sizeof(*entry));

    while (offset < size) {
        const unsigned char *tag;
        uint64_t chunk_size;
        size_t chunk_length;
        if (size - offset < 12) {
            return fail_message("truncated XP3 chunk header");
        }
        tag = payload + offset;
        chunk_size = read_u64_le(payload + offset + 4);
        if (chunk_size > SIZE_MAX || chunk_size > size - offset - 12) {
            return fail_message("truncated XP3 chunk payload");
        }
        chunk_length = (size_t)chunk_size;
        offset += 12;
        if (memcmp(tag, "info", 4) == 0) {
            info_payload = payload + offset;
            info_size = chunk_length;
        } else if (memcmp(tag, "segm", 4) == 0) {
            segm_payload = payload + offset;
            segm_size = chunk_length;
        } else if (memcmp(tag, "adlr", 4) == 0) {
            adlr_payload = payload + offset;
            adlr_size = chunk_length;
        }
        offset += chunk_length;
    }

    if (info_payload == NULL || segm_payload == NULL) {
        return fail_message("XP3 file record is missing info or segm chunk");
    }
    if (!parse_info_chunk(info_payload, info_size, entry)) {
        return 0;
    }
    if (!parse_segments_chunk(segm_payload, segm_size, entry)) {
        return 0;
    }
    if (adlr_payload != NULL) {
        if (adlr_size < 4) {
            return fail_message("adlr chunk is too short");
        }
        entry->adler32 = read_u32_le(adlr_payload);
        entry->has_adler = 1;
    }
    return 1;
}

static int parse_index_blob(const unsigned char *blob, size_t size, XP3Archive *archive) {
    size_t offset = 0;
    while (offset < size) {
        const unsigned char *tag;
        uint64_t chunk_size;
        size_t chunk_length;
        if (size - offset < 12) {
            return fail_message("truncated XP3 chunk header");
        }
        tag = blob + offset;
        chunk_size = read_u64_le(blob + offset + 4);
        if (chunk_size > SIZE_MAX || chunk_size > size - offset - 12) {
            return fail_message("truncated XP3 chunk payload");
        }
        chunk_length = (size_t)chunk_size;
        offset += 12;
        if (memcmp(tag, "File", 4) == 0) {
            XP3Entry entry;
            if (!parse_file_record(blob + offset, chunk_length, &entry)) {
                free_entry(&entry);
                return 0;
            }
            if (!archive_append_entry(archive, &entry)) {
                free_entry(&entry);
                return 0;
            }
        }
        offset += chunk_length;
    }
    return 1;
}

static int find_xp3_offset(const unsigned char *data, size_t size, uint64_t *offset_out) {
    size_t signature_len = sizeof(XP3_SIGNATURE);
    if (size >= signature_len && memcmp(data, XP3_SIGNATURE, signature_len) == 0) {
        *offset_out = 0;
        return 1;
    }
    if (size < 2 || data[0] != 'M' || data[1] != 'Z') {
        return fail_message("file is neither an XP3 archive nor an MZ-wrapped XP3");
    }
    for (size_t base = 16; base + signature_len <= size; base += 16) {
        if (memcmp(data + base, XP3_SIGNATURE, signature_len) == 0) {
            *offset_out = (uint64_t)base;
            return 1;
        }
    }
    return fail_message("XP3 signature not found");
}

static int open_xp3_archive(const wchar_t *path, XP3Archive *archive) {
    unsigned char *data;
    size_t size;
    uint64_t current_offset;
    memset(archive, 0, sizeof(*archive));
    archive->mapped.file = INVALID_HANDLE_VALUE;
    archive->mapped.mapping = INVALID_HANDLE_VALUE;
    archive->path = dup_wstring(path);
    if (archive->path == NULL) {
        return 0;
    }
    if (!open_mapped_file(path, &archive->mapped)) {
        free_archive(archive);
        return 0;
    }
    data = archive->mapped.view;
    size = archive->mapped.size;
    if (!find_xp3_offset(data, size, &archive->xp3_offset)) {
        free_archive(archive);
        return 0;
    }
    if (archive->xp3_offset + sizeof(XP3_SIGNATURE) + 8 > size) {
        free_archive(archive);
        return fail_message("XP3 header is truncated");
    }
    archive->index_offset = read_u64_le(data + archive->xp3_offset + sizeof(XP3_SIGNATURE));
    current_offset = archive->index_offset;
    while (1) {
        uint64_t absolute = archive->xp3_offset + current_offset;
        uint8_t flag;
        uint32_t compression_mode;
        int has_next;
        if (absolute + 1 > size) {
            free_archive(archive);
            return fail_message("index record is truncated");
        }
        flag = data[absolute];
        compression_mode = flag & 0x7U;
        has_next = (flag & 0x80U) != 0;
        if (compression_mode == 0) {
            uint64_t raw_size;
            size_t chunk_size;
            if (absolute + 9 > size) {
                free_archive(archive);
                return fail_message("index header is truncated");
            }
            raw_size = read_u64_le(data + absolute + 1);
            if (raw_size > SIZE_MAX || absolute + 9 + raw_size > size) {
                free_archive(archive);
                return fail_message("raw index blob is truncated");
            }
            chunk_size = (size_t)raw_size;
            if (!parse_index_blob(data + absolute + 9, chunk_size, archive)) {
                free_archive(archive);
                return 0;
            }
            if (!has_next) {
                break;
            }
            if (absolute + 9 + raw_size + 8 > size) {
                free_archive(archive);
                return fail_message("next index pointer is truncated");
            }
            current_offset = read_u64_le(data + absolute + 9 + raw_size);
        } else if (compression_mode == 1) {
            uint64_t compressed_size;
            uint64_t raw_size;
            unsigned char *index_blob;
            mz_ulong out_size;
            int status;
            if (absolute + 17 > size) {
                free_archive(archive);
                return fail_message("compressed index header is truncated");
            }
            compressed_size = read_u64_le(data + absolute + 1);
            raw_size = read_u64_le(data + absolute + 9);
            if (compressed_size > SIZE_MAX || raw_size > SIZE_MAX || absolute + 17 + compressed_size > size) {
                free_archive(archive);
                return fail_message("compressed index blob is truncated");
            }
            if (raw_size > UINT32_MAX) {
                free_archive(archive);
                return fail_message("compressed index blob is too large");
            }
            index_blob = (unsigned char *)checked_malloc((size_t)raw_size == 0 ? 1 : (size_t)raw_size);
            if (index_blob == NULL) {
                free_archive(archive);
                return 0;
            }
            out_size = (mz_ulong)raw_size;
            status = mz_uncompress(index_blob, &out_size, data + absolute + 17, (mz_ulong)compressed_size);
            if (status != MZ_OK || out_size != raw_size) {
                free(index_blob);
                free_archive(archive);
                return fail_message("cannot decompress XP3 index blob");
            }
            archive->index_compressed = 1;
            if (!parse_index_blob(index_blob, (size_t)raw_size, archive)) {
                free(index_blob);
                free_archive(archive);
                return 0;
            }
            free(index_blob);
            if (!has_next) {
                break;
            }
            if (absolute + 17 + compressed_size + 8 > size) {
                free_archive(archive);
                return fail_message("next index pointer is truncated");
            }
            current_offset = read_u64_le(data + absolute + 17 + compressed_size);
        } else {
            free_archive(archive);
            return fail_message("unsupported XP3 index compression mode");
        }
    }
    return 1;
}

static int read_entry_data(const XP3Archive *archive, const XP3Entry *entry, unsigned char **data_out, size_t *size_out) {
    unsigned char *merged;
    size_t write_offset = 0;
    if (entry->original_size > SIZE_MAX) {
        return fail_message("file is too large for this build");
    }
    merged = (unsigned char *)checked_malloc((size_t)entry->original_size == 0 ? 1 : (size_t)entry->original_size);
    if (merged == NULL) {
        return 0;
    }
    for (size_t i = 0; i < entry->segment_count; ++i) {
        const XP3Segment *segment = &entry->segments[i];
        uint64_t start = archive->xp3_offset + segment->offset;
        if (start + segment->archived_size > archive->mapped.size) {
            free(merged);
            return fail_message("segment payload is truncated");
        }
        if (segment->original_size > SIZE_MAX || segment->archived_size > UINT32_MAX || segment->original_size > UINT32_MAX) {
            free(merged);
            return fail_message("segment is too large for this build");
        }
        if (segment->compressed) {
            mz_ulong out_size = (mz_ulong)segment->original_size;
            int status = mz_uncompress(
                merged + write_offset,
                &out_size,
                archive->mapped.view + start,
                (mz_ulong)segment->archived_size);
            if (status != MZ_OK || out_size != segment->original_size) {
                free(merged);
                return fail_message("cannot decompress XP3 segment");
            }
        } else {
            if (segment->archived_size != segment->original_size) {
                free(merged);
                return fail_message("raw segment size mismatch");
            }
            memcpy(merged + write_offset, archive->mapped.view + start, (size_t)segment->original_size);
        }
        write_offset += (size_t)segment->original_size;
    }
    if (write_offset != entry->original_size) {
        free(merged);
        return fail_message("file size mismatch");
    }
    *data_out = merged;
    *size_out = write_offset;
    return 1;
}

static int write_entire_file(const wchar_t *path, const unsigned char *data, size_t size) {
    FILE *fp;
    if (!ensure_parent_directory(path)) {
        return 0;
    }
    fp = _wfopen(path, L"wb");
    if (fp == NULL) {
        return fail_win32(L"cannot create file", path);
    }
    if (size > 0 && fwrite(data, 1, size, fp) != size) {
        fclose(fp);
        return fail_win32(L"cannot write file", path);
    }
    fclose(fp);
    return 1;
}

static int read_entire_file(const wchar_t *path, unsigned char **data_out, size_t *size_out) {
    FILE *fp = _wfopen(path, L"rb");
    unsigned char *data;
    __int64 length;
    if (fp == NULL) {
        return fail_win32(L"cannot open file", path);
    }
    if (_fseeki64(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return fail_win32(L"cannot seek file", path);
    }
    length = _ftelli64(fp);
    if (length < 0) {
        fclose(fp);
        return fail_win32(L"cannot tell file size", path);
    }
    if ((uint64_t)length > SIZE_MAX) {
        fclose(fp);
        return fail_message("file is too large for this build");
    }
    if (_fseeki64(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        return fail_win32(L"cannot seek file", path);
    }
    data = (unsigned char *)checked_malloc((size_t)length == 0 ? 1 : (size_t)length);
    if (data == NULL) {
        fclose(fp);
        return 0;
    }
    if (length > 0 && fread(data, 1, (size_t)length, fp) != (size_t)length) {
        free(data);
        fclose(fp);
        return fail_win32(L"cannot read file", path);
    }
    fclose(fp);
    *data_out = data;
    *size_out = (size_t)length;
    return 1;
}

static int append_chunk(ByteBuffer *target, const char tag[4], const ByteBuffer *payload) {
    if (!buffer_append(target, tag, 4)) {
        return 0;
    }
    if (!buffer_append_u64(target, (uint64_t)payload->size)) {
        return 0;
    }
    return buffer_append(target, payload->data, payload->size);
}

static int build_file_record(
    ByteBuffer *target,
    const wchar_t *name,
    uint32_t info_flags,
    uint64_t segment_offset,
    const unsigned char *original_data,
    size_t original_size,
    size_t stored_size,
    int compressed,
    uint32_t adler32_value) {
    ByteBuffer info = {0};
    ByteBuffer segm = {0};
    ByteBuffer adlr = {0};
    ByteBuffer file_payload = {0};
    size_t name_units = wcslen(name);
    int ok = 0;
    if (!buffer_append_u32(&info, info_flags)
        || !buffer_append_u64(&info, (uint64_t)original_size)
        || !buffer_append_u64(&info, (uint64_t)stored_size)
        || !buffer_append_u16(&info, (uint16_t)name_units)) {
        goto cleanup;
    }
    for (size_t i = 0; i < name_units; ++i) {
        if (!buffer_append_u16(&info, (uint16_t)name[i])) {
            goto cleanup;
        }
    }
    if (!buffer_append_u32(&segm, compressed ? 1U : 0U)
        || !buffer_append_u64(&segm, segment_offset)
        || !buffer_append_u64(&segm, (uint64_t)original_size)
        || !buffer_append_u64(&segm, (uint64_t)stored_size)
        || !buffer_append_u32(&adlr, adler32_value)) {
        goto cleanup;
    }
    if (!append_chunk(&file_payload, "info", &info)
        || !append_chunk(&file_payload, "segm", &segm)
        || !append_chunk(&file_payload, "adlr", &adlr)
        || !append_chunk(target, "File", &file_payload)) {
        goto cleanup;
    }
    ok = 1;

cleanup:
    (void)original_data;
    buffer_free(&info);
    buffer_free(&segm);
    buffer_free(&adlr);
    buffer_free(&file_payload);
    return ok;
}

static void free_input_file_list(InputFileList *list) {
    for (size_t i = 0; i < list->count; ++i) {
        free(list->items[i].abs_path);
        free(list->items[i].rel_name);
    }
    free(list->items);
    list->items = NULL;
    list->count = 0;
    list->capacity = 0;
}

static int input_file_list_append(InputFileList *list, const wchar_t *abs_path, const wchar_t *rel_name) {
    InputFile *items;
    size_t capacity = list->capacity == 0 ? 64 : list->capacity * 2;
    if (list->count == list->capacity) {
        items = (InputFile *)checked_realloc(list->items, capacity * sizeof(InputFile));
        if (items == NULL) {
            return 0;
        }
        list->items = items;
        list->capacity = capacity;
    }
    list->items[list->count].abs_path = dup_wstring(abs_path);
    list->items[list->count].rel_name = dup_wstring(rel_name);
    if (list->items[list->count].abs_path == NULL || list->items[list->count].rel_name == NULL) {
        free(list->items[list->count].abs_path);
        free(list->items[list->count].rel_name);
        return 0;
    }
    list->count += 1;
    return 1;
}

static int compare_input_files(const void *left, const void *right) {
    const InputFile *a = (const InputFile *)left;
    const InputFile *b = (const InputFile *)right;
    return wcscmp(a->rel_name, b->rel_name);
}

static int collect_input_files_recursive(const wchar_t *current_abs, const wchar_t *current_rel, InputFileList *list) {
    WIN32_FIND_DATAW find_data;
    HANDLE handle;
    wchar_t *pattern = join_paths(current_abs, L"*", L'\\', 0);
    if (pattern == NULL) {
        return 0;
    }
    handle = FindFirstFileW(pattern, &find_data);
    free(pattern);
    if (handle == INVALID_HANDLE_VALUE) {
        return fail_win32(L"cannot enumerate directory", current_abs);
    }
    do {
        wchar_t *child_abs;
        wchar_t *child_rel;
        if (wcscmp(find_data.cFileName, L".") == 0 || wcscmp(find_data.cFileName, L"..") == 0) {
            continue;
        }
        if (wcscmp(find_data.cFileName, META_FILENAME) == 0) {
            continue;
        }
        child_abs = join_paths(current_abs, find_data.cFileName, L'\\', 0);
        child_rel = current_rel[0] == 0
            ? dup_wstring(find_data.cFileName)
            : join_paths(current_rel, find_data.cFileName, L'/', 1);
        if (child_abs == NULL || child_rel == NULL) {
            free(child_abs);
            free(child_rel);
            FindClose(handle);
            return 0;
        }
        if (find_data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            int ok = collect_input_files_recursive(child_abs, child_rel, list);
            free(child_abs);
            free(child_rel);
            if (!ok) {
                FindClose(handle);
                return 0;
            }
        } else {
            int ok = input_file_list_append(list, child_abs, child_rel);
            free(child_abs);
            free(child_rel);
            if (!ok) {
                FindClose(handle);
                return 0;
            }
        }
    } while (FindNextFileW(handle, &find_data));
    FindClose(handle);
    return 1;
}

static int collect_input_files(const wchar_t *root, InputFileList *list) {
    memset(list, 0, sizeof(*list));
    if (!collect_input_files_recursive(root, L"", list)) {
        free_input_file_list(list);
        return 0;
    }
    qsort(list->items, list->count, sizeof(InputFile), compare_input_files);
    return 1;
}

static int write_u64_file(FILE *fp, uint64_t value) {
    unsigned char bytes[8];
    for (int i = 0; i < 8; ++i) {
        bytes[i] = (unsigned char)((value >> (8 * i)) & 0xFF);
    }
    return fwrite(bytes, 1, sizeof(bytes), fp) == sizeof(bytes);
}

static int command_unpack(const wchar_t *archive_path, const wchar_t *output_dir) {
    XP3Archive archive;
    ExtransContext context;
    ProgressBar progress;
    int ok = 0;
    printf("[INFO] opening archive\n");
    if (!ensure_directory_recursive(output_dir)) {
        return 0;
    }
    if (!open_xp3_archive(archive_path, &archive)) {
        return 0;
    }
    build_extrans_context(&context);
    printf("[INFO] using filter profile ballet-style\n");
    printf("[INFO] unpacking %" PRIu64 " files\n", (uint64_t)archive.entry_count);
    progress_init(&progress, "unpacking", archive.entry_count);

    for (size_t i = 0; i < archive.entry_count; ++i) {
        unsigned char *data = NULL;
        size_t size = 0;
        wchar_t *target_path = NULL;
        if (!read_entry_data(&archive, &archive.entries[i], &data, &size)) {
            goto cleanup;
        }
        if (archive.entries[i].has_adler) {
            apply_extrans_filter(data, size, archive.entries[i].adler32, &context);
        }
        target_path = build_output_path(output_dir, archive.entries[i].name);
        if (target_path == NULL) {
            free(data);
            goto cleanup;
        }
        if (!write_entire_file(target_path, data, size)) {
            free(target_path);
            free(data);
            goto cleanup;
        }
        free(target_path);
        free(data);
        progress_advance(&progress, 1);
    }
    if (archive.entry_count == 0) {
        progress_finish(&progress);
    }
    printf("applied extrans filter profile ballet-style\n");
    printf("unpacked %" PRIu64 " files\n", (uint64_t)archive.entry_count);
    ok = 1;

cleanup:
    free_archive(&archive);
    return ok;
}

static int compress_if_smaller(const unsigned char *source, size_t source_size, unsigned char **stored_data, size_t *stored_size, int *compressed_out) {
    unsigned char *compressed = NULL;
    mz_ulong compressed_bound;
    mz_ulong compressed_size;
    int status;
    if (source_size > UINT32_MAX) {
        return fail_message("file is too large for compression in this build");
    }
    compressed_bound = mz_compressBound((mz_ulong)source_size);
    compressed = (unsigned char *)checked_malloc((size_t)compressed_bound == 0 ? 1 : (size_t)compressed_bound);
    if (compressed == NULL) {
        return 0;
    }
    compressed_size = compressed_bound;
    status = mz_compress2(compressed, &compressed_size, source, (mz_ulong)source_size, 9);
    if (status != MZ_OK) {
        free(compressed);
        return fail_message("compression failed");
    }
    if ((size_t)compressed_size < source_size) {
        *stored_data = compressed;
        *stored_size = (size_t)compressed_size;
        *compressed_out = 1;
        return 1;
    }
    free(compressed);
    *stored_data = (unsigned char *)checked_malloc(source_size == 0 ? 1 : source_size);
    if (*stored_data == NULL) {
        return 0;
    }
    if (source_size > 0) {
        memcpy(*stored_data, source, source_size);
    }
    *stored_size = source_size;
    *compressed_out = 0;
    return 1;
}

static int command_pack(const wchar_t *input_dir, const wchar_t *output_path) {
    InputFileList files;
    FILE *fp = NULL;
    ByteBuffer index_blob = {0};
    unsigned char *compressed_index = NULL;
    size_t compressed_index_size = 0;
    int compressed_index_used = 0;
    uint64_t index_offset;
    ExtransContext context;
    ProgressBar progress;
    int ok = 0;

    build_extrans_context(&context);
    if (!collect_input_files(input_dir, &files)) {
        return 0;
    }
    if (!ensure_parent_directory(output_path)) {
        free_input_file_list(&files);
        return 0;
    }
    fp = _wfopen(output_path, L"wb");
    if (fp == NULL) {
        free_input_file_list(&files);
        return fail_win32(L"cannot create archive", output_path);
    }

    printf("[INFO] using filter profile ballet-style\n");
    printf("[INFO] packing %" PRIu64 " files\n", (uint64_t)files.count);
    if (fwrite(XP3_SIGNATURE, 1, sizeof(XP3_SIGNATURE), fp) != sizeof(XP3_SIGNATURE) || !write_u64_file(fp, 0)) {
        goto cleanup;
    }
    progress_init(&progress, "packing", files.count);

    for (size_t i = 0; i < files.count; ++i) {
        unsigned char *original_data = NULL;
        unsigned char *stored_source = NULL;
        unsigned char *stored_data = NULL;
        size_t original_size = 0;
        size_t stored_size = 0;
        uint32_t adler32_value;
        uint64_t segment_offset;
        int compressed = 0;

        if (!read_entire_file(files.items[i].abs_path, &original_data, &original_size)) {
            goto cleanup;
        }
        adler32_value = (uint32_t)mz_adler32(MZ_ADLER32_INIT, original_data, original_size);
        stored_source = (unsigned char *)checked_malloc(original_size == 0 ? 1 : original_size);
        if (stored_source == NULL) {
            free(original_data);
            goto cleanup;
        }
        if (original_size > 0) {
            memcpy(stored_source, original_data, original_size);
        }
        reverse_extrans_filter(stored_source, original_size, adler32_value, &context);
        if (!compress_if_smaller(stored_source, original_size, &stored_data, &stored_size, &compressed)) {
            free(stored_source);
            free(original_data);
            goto cleanup;
        }
        segment_offset = (uint64_t)_ftelli64(fp);
        if (stored_size > 0 && fwrite(stored_data, 1, stored_size, fp) != stored_size) {
            free(stored_data);
            free(stored_source);
            free(original_data);
            goto cleanup;
        }
        if (!build_file_record(
                &index_blob,
                files.items[i].rel_name,
                0,
                segment_offset,
                original_data,
                original_size,
                stored_size,
                compressed,
                adler32_value)) {
            free(stored_data);
            free(stored_source);
            free(original_data);
            goto cleanup;
        }
        free(stored_data);
        free(stored_source);
        free(original_data);
        progress_advance(&progress, 1);
    }
    if (files.count == 0) {
        progress_finish(&progress);
    }

    index_offset = (uint64_t)_ftelli64(fp);
    if (!compress_if_smaller(index_blob.data, index_blob.size, &compressed_index, &compressed_index_size, &compressed_index_used)) {
        goto cleanup;
    }
    if (fwrite(compressed_index_used ? "\x01" : "\x00", 1, 1, fp) != 1) {
        goto cleanup;
    }
    if (compressed_index_used) {
        if (!write_u64_file(fp, (uint64_t)compressed_index_size) || !write_u64_file(fp, (uint64_t)index_blob.size)) {
            goto cleanup;
        }
    } else {
        if (!write_u64_file(fp, (uint64_t)index_blob.size)) {
            goto cleanup;
        }
    }
    if ((compressed_index_used ? compressed_index_size : index_blob.size) > 0) {
        size_t payload_size = compressed_index_used ? compressed_index_size : index_blob.size;
        const unsigned char *payload = compressed_index_used ? compressed_index : index_blob.data;
        if (fwrite(payload, 1, payload_size, fp) != payload_size) {
            goto cleanup;
        }
    }
    if (_fseeki64(fp, (long long)sizeof(XP3_SIGNATURE), SEEK_SET) != 0 || !write_u64_file(fp, index_offset)) {
        goto cleanup;
    }

    printf("packed %" PRIu64 " files\n", (uint64_t)files.count);
    ok = 1;

cleanup:
    free(compressed_index);
    buffer_free(&index_blob);
    free_input_file_list(&files);
    if (fp != NULL) {
        fclose(fp);
    }
    if (!ok) {
        fwprintf(stderr, L"error: pack failed\n");
    }
    return ok;
}

static void print_usage(void) {
    printf("xp3_tool.exe <file-or-folder>\n");
    printf("xp3_tool.exe unpack <archive.xp3> [output_dir]\n");
    printf("xp3_tool.exe pack <input_dir> [output.xp3]\n");
}

static int is_directory_path(const wchar_t *path) {
    DWORD attributes = GetFileAttributesW(path);
    if (attributes == INVALID_FILE_ATTRIBUTES) {
        return 0;
    }
    return (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
}

static int run_auto_mode(const wchar_t *source) {
    wchar_t *output = NULL;
    int ok;
    if (is_directory_path(source)) {
        output = path_with_extension(source, L".xp3");
        if (output == NULL) {
            return 0;
        }
        printf("[INFO] auto mode: packing\n");
        ok = command_pack(source, output);
        free(output);
        return ok;
    }
    output = path_without_extension(source);
    if (output == NULL) {
        return 0;
    }
    printf("[INFO] auto mode: unpacking\n");
    ok = command_unpack(source, output);
    free(output);
    return ok;
}

int wmain(int argc, wchar_t **argv) {
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);

    if (argc == 2) {
        return run_auto_mode(argv[1]) ? 0 : 1;
    }
    if (argc >= 3 && wcscmp(argv[1], L"unpack") == 0) {
        wchar_t *output = (argc >= 4) ? dup_wstring(argv[3]) : path_without_extension(argv[2]);
        int ok;
        if (output == NULL) {
            return 1;
        }
        ok = command_unpack(argv[2], output);
        free(output);
        return ok ? 0 : 1;
    }
    if (argc >= 3 && wcscmp(argv[1], L"pack") == 0) {
        wchar_t *output = (argc >= 4) ? dup_wstring(argv[3]) : path_with_extension(argv[2], L".xp3");
        int ok;
        if (output == NULL) {
            return 1;
        }
        ok = command_pack(argv[2], output);
        free(output);
        return ok ? 0 : 1;
    }

    print_usage();
    return 1;
}