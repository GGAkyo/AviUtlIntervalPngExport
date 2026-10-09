// Independent implementation of the ExEdit 0.92 text layout and IPC.
// Offsets checked against ePi5131/aviutl_exedit_sdk and hebiiro/Common-Library.
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <memory>
#include <algorithm>

static unsigned citation_epoch = 0;
struct CitationRow {
    int index, filter, start, end, layer, midpoint;
    std::wstring text;
    bool same(const CitationRow& other) const {
        return index == other.index && filter == other.filter && start == other.start
            && end == other.end && layer == other.layer && midpoint == other.midpoint && text == other.text;
    }
};
static int32_t read_i32(const unsigned char* p) {
    int32_t result; memcpy(&result, p, sizeof(result)); return result;
}
static bool readable(const void* data, size_t length) {
    MEMORY_BASIC_INFORMATION m = {};
    if (!VirtualQuery(data, &m, sizeof(m)) || m.State != MEM_COMMIT
        || (m.Protect & (PAGE_NOACCESS | PAGE_GUARD))) return false;
    return reinterpret_cast<uintptr_t>(data) + length <= reinterpret_cast<uintptr_t>(m.BaseAddress) + m.RegionSize;
}
static unsigned char* object_at(unsigned char* base, int index) {
    int count = read_i32(base + 0x146250);
    if (index < 0 || index >= count || count > 100000) throw std::runtime_error("Invalid object index.");
    auto table = *reinterpret_cast<unsigned char**>(base + 0x1E0FA4);
    auto object = table + index * 1480;
    if (!readable(object, 1480)) throw std::runtime_error("Invalid object table.");
    return object;
}
static wchar_t* text_at(unsigned char* base, int index, int filter) {
    auto object = object_at(base, index);
    if (filter < 0 || filter >= 12 || read_i32(object + 84 + filter * 12) != 3)
        throw std::runtime_error("Not an ExEdit text filter.");
    uint32_t offset = static_cast<uint32_t>(read_i32(object + 1208));
    uint32_t local = static_cast<uint32_t>(read_i32(object + 92 + filter * 12));
    uint32_t size = static_cast<uint32_t>(read_i32(object + 244));
    if (local > size || size - local < 2100) throw std::runtime_error("Invalid text data size.");
    auto pool = *reinterpret_cast<unsigned char**>(base + 0x1E0FA8);
    auto text = reinterpret_cast<wchar_t*>(pool + offset + local + 4 + 48);
    if (!readable(text, 2048)) throw std::runtime_error("Invalid text data.");
    return text;
}
static std::vector<CitationRow> citation_rows(unsigned char* base, int scene) {
    std::vector<CitationRow> result;
    int count = read_i32(base + 0x146250);
    if (count < 0 || count > 100000) throw std::runtime_error("Invalid object count.");
    for (int i = 0; i < count; ++i) {
        auto object = object_at(base, i);
        if (!(read_i32(object) & 1) || read_i32(object + 1476) != scene) continue;
        int leader = read_i32(object + 80);
        if (leader >= 0 && leader != i) continue;
        for (int f = 0; f < 12; ++f) {
            int type = read_i32(object + 84 + f * 12);
            if (type < 0) break;
            if (type != 3) continue;
            auto text = text_at(base, i, f);
            size_t length = 0; while (length < 1024 && text[length]) ++length;
            if (length >= 1024) throw std::runtime_error("Text is not terminated.");
            result.push_back({i, f, read_i32(object + 8), read_i32(object + 12),
                read_i32(object + 1472), read_i32(object + 80), std::wstring(text, length)});
        }
    }
    return result;
}
static void put_u32(std::vector<unsigned char>& data, uint32_t value) {
    auto p = reinterpret_cast<unsigned char*>(&value); data.insert(data.end(), p, p + 4);
}
static void put_text(std::vector<unsigned char>& data, const std::wstring& value) {
    put_u32(data, static_cast<uint32_t>(value.size()));
    auto p = reinterpret_cast<const unsigned char*>(value.data()); data.insert(data.end(), p, p + value.size() * 2);
}
struct CitationBridge {
    FILTER* fp;
    void* editp;
    unsigned char* base;
    int scene;
    unsigned epoch;
    std::wstring project;
    std::vector<CitationRow> rows;
    uint32_t token[4];
    HANDLE process = nullptr;
    bool window_owned = false;
    ~CitationBridge() { if (process) CloseHandle(process); }
};
struct PacketReader {
    const unsigned char* p; size_t left;
    uint32_t number() {
        if (left < 4) throw std::runtime_error("Truncated packet.");
        uint32_t result; memcpy(&result, p, 4); p += 4; left -= 4; return result;
    }
    std::wstring text() {
        auto n = number();
        if (n > 1023 || left < n * 2) throw std::runtime_error("Invalid text length.");
        std::wstring result(n, 0); memcpy(result.data(), p, n * 2); p += n * 2; left -= n * 2;
        if (result.find(L'\0') != std::wstring::npos) throw std::runtime_error("Embedded NUL.");
        return result;
    }
};
static LRESULT apply_citations(CitationBridge& state, const COPYDATASTRUCT* packet) {
    if (!packet || packet->dwData != 0x45504355 || packet->cbData > 8 * 1024 * 1024 || !packet->lpData) return 0;
    PacketReader reader{static_cast<unsigned char*>(packet->lpData), packet->cbData};
    if (reader.number() != 1) return 0;
    for (auto value : state.token) if (reader.number() != value) return 0;
    SYS_INFO info = {};
    if (citation_epoch != state.epoch || !state.fp->exfunc->get_sys_info(state.editp, &info)
        || wide(info.project_name) != state.project || read_i32(state.base + 0x1A5310) != state.scene) return 2;
    auto current = citation_rows(state.base, state.scene);
    if (current.size() != state.rows.size()) return 2;
    for (size_t i = 0; i < current.size(); ++i) if (!current[i].same(state.rows[i])) return 2;
    auto n = reader.number();
    if (n > state.rows.size()) return 0;
    std::vector<std::pair<size_t, std::wstring>> changes;
    std::vector<size_t> seen;
    for (uint32_t i = 0; i < n; ++i) {
        int index = reader.number(), filter = reader.number(); auto replacement = reader.text();
        auto found = std::find_if(state.rows.begin(), state.rows.end(), [&](const auto& row) {
            return row.index == index && row.filter == filter;
        });
        if (found == state.rows.end() || found->midpoint >= 0) return 0;
        size_t position = found - state.rows.begin();
        if (std::find(seen.begin(), seen.end(), position) != seen.end()) return 0;
        seen.push_back(position);
        if (found->text != replacement) changes.emplace_back(position, std::move(replacement));
    }
    if (reader.left) return 0;
    if (changes.empty()) return 1;
    // Allocate all expected-state replacements before beginning the undo block.
    auto expected = state.rows;
    for (const auto& change : changes) expected[change.first].text = change.second;
    auto push_undo = reinterpret_cast<void(__cdecl*)()>(state.base + 0x8D150);
    auto create_undo = reinterpret_cast<void(__cdecl*)(int, UINT)>(state.base + 0x8D290);
    push_undo();
    for (const auto& change : changes) create_undo(state.rows[change.first].index, 1);
    std::vector<wchar_t*> targets;
    targets.reserve(changes.size());
    for (const auto& change : changes) {
        const auto& row = state.rows[change.first];
        targets.push_back(text_at(state.base, row.index, row.filter));
    }
    size_t target_index = 0;
    for (const auto& change : changes) {
        auto target = targets[target_index++];
        memset(target, 0, 2048); memcpy(target, change.second.data(), change.second.size() * 2);
    }
    state.rows.swap(expected);
    int selected = read_i32(state.base + 0x177A10);
    if (selected >= 0 && selected < read_i32(state.base + 0x146250))
        reinterpret_cast<void(__cdecl*)(int)>(state.base + 0x39490)(selected);
    HWND exedit_window = *reinterpret_cast<HWND*>(state.base + 0x177A44);
    if (exedit_window) InvalidateRect(exedit_window, nullptr, FALSE);
    // Refresh the host at its unchanged current frame.
    state.fp->exfunc->set_frame(state.editp, state.fp->exfunc->get_frame(state.editp));
    return 1;
}
static LRESULT CALLBACK citation_window(HWND hwnd, UINT message, WPARAM wp, LPARAM lp) {
    auto state = reinterpret_cast<CitationBridge*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        auto create = reinterpret_cast<CREATESTRUCTW*>(lp);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
    }
    if (message == WM_COPYDATA && state) {
        try { return apply_citations(*state, reinterpret_cast<COPYDATASTRUCT*>(lp)); }
        catch (...) { return 0; }
    }
    if (message == WM_TIMER && state && state->process && WaitForSingleObject(state->process, 0) == WAIT_OBJECT_0)
        DestroyWindow(hwnd);
    if (message == WM_NCDESTROY) {
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0); if (state && state->window_owned) delete state;
    }
    return DefWindowProcW(hwnd, message, wp, lp);
}
static void launch_citations(HWND owner, FILTER* fp, void* editp) {
    SYS_INFO info = {};
    if (!editp || !fp->exfunc || !fp->exfunc->get_sys_info(editp, &info)) throw std::runtime_error("Open a project first.");
    auto save = find_save_exo(fp, info);
    if (!locate_tool(owner, fp)) return;
    auto python = tool_directory + L"\\.venv\\Scripts\\pythonw.exe";
    auto gui = tool_directory + L"\\citation_gui.py";
    if (!is_file(python) || !is_file(gui)) throw std::runtime_error("Run setup.cmd and keep citation_gui.py in the tool folder.");
    auto state = std::make_unique<CitationBridge>();
    state->fp = fp; state->editp = editp;
    state->base = reinterpret_cast<unsigned char*>(GetModuleHandleW(L"exedit.auf"));
    state->scene = read_i32(state->base + 0x1A5310); state->epoch = citation_epoch; state->project = wide(info.project_name);
    state->rows = citation_rows(state->base, state->scene);
    if (state->rows.empty()) throw std::runtime_error("No text objects in the current scene.");
    HMODULE advapi = LoadLibraryW(L"advapi32.dll");
    auto random = advapi ? reinterpret_cast<BOOLEAN(WINAPI*)(PVOID, ULONG)>(GetProcAddress(advapi, "SystemFunction036")) : nullptr;
    bool generated = random && random(state->token, sizeof(state->token));
    if (advapi) FreeLibrary(advapi);
    if (!generated) throw std::runtime_error("Cannot generate IPC session token.");
    Snapshot snapshot; snapshot.create();
    if (!save(ansi_path(snapshot.source).c_str())) throw std::runtime_error("Could not capture current scene.");
    std::vector<unsigned char> data;
    put_u32(data, 0x45504349); put_u32(data, 1);
    for (auto value : state->token) put_u32(data, value);
    put_u32(data, state->scene); put_u32(data, static_cast<uint32_t>(state->rows.size()));
    for (const auto& row : state->rows) {
        for (int value : {row.index, row.filter, row.start, row.end, row.layer, row.midpoint}) put_u32(data, value);
        put_text(data, row.text);
    }
    FILE* file = nullptr;
    _wfopen_s(&file, (snapshot.folder + L"\\citation.input").c_str(), L"wb");
    if (!file) throw std::runtime_error("Cannot write citation snapshot.");
    bool written = fwrite(data.data(), 1, data.size(), file) == data.size(); fclose(file);
    if (!written) throw std::runtime_error("Cannot write citation snapshot.");
    WNDCLASSW wc = {}; wc.lpfnWndProc = citation_window; wc.hInstance = fp->dll_hinst; wc.lpszClassName = L"EpeCitationBridgeV1";
    RegisterClassW(&wc);
    HWND bridge = CreateWindowExW(0, wc.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, fp->dll_hinst, state.get());
    if (!bridge) throw std::runtime_error("Cannot create citation bridge.");
    auto owned = state.release();
    owned->window_owned = true;
    wchar_t cwd[32768] = {}; GetCurrentDirectoryW(32768, cwd);
    auto catalog = owned->project.empty() ? std::wstring(cwd) + L"\\references.json"
        : owned->project.substr(0, owned->project.find_last_of(L'.')) + L".references.json";
    std::wstring command = quote(python) + L" -X utf8 " + quote(gui) + L" --source " + quote(snapshot.source)
        + L" --snapshot " + quote(snapshot.folder + L"\\citation.input")
        + L" --target " + std::to_wstring(reinterpret_cast<uintptr_t>(bridge)) + L" --references " + quote(catalog);
    std::vector<wchar_t> mutable_command(command.begin(), command.end()); mutable_command.push_back(0);
    STARTUPINFOW startup = {}; startup.cb = sizeof(startup); PROCESS_INFORMATION process = {};
    if (!CreateProcessW(python.c_str(), mutable_command.data(), nullptr, nullptr, FALSE, CREATE_NO_WINDOW,
                        nullptr, tool_directory.c_str(), &startup, &process)) {
        DestroyWindow(bridge); throw std::runtime_error("Cannot start citation manager.");
    }
    owned->process = process.hProcess; CloseHandle(process.hThread); SetTimer(bridge, 1, 500, nullptr);
    snapshot.handed_off = true;
}
