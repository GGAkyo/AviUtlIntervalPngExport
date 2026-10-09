// AviUtl 1 (x86): read-only current-scene snapshot and converter launcher.
// Private SaveExo ABI is supported only for ExEdit 0.92.
#include <windows.h>
#include <commdlg.h>
#include <string>
#include <vector>
#include <stdexcept>
#include "filter.h"

static std::wstring tool_directory;
static char menu_name[128] = {};

static bool is_file(const std::wstring& path) {
    DWORD attr = GetFileAttributesW(path.c_str());
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

static std::wstring parent(const std::wstring& path) {
    auto pos = path.find_last_of(L"\\/");
    return pos == std::wstring::npos ? L"" : path.substr(0, pos);
}

static std::wstring wide(const char* value) {
    if (!value || !*value) return L"";
    int n = MultiByteToWideChar(932, 0, value, -1, nullptr, 0);
    std::wstring result(n, 0);
    MultiByteToWideChar(932, 0, value, -1, &result[0], n);
    result.resize(n - 1);
    return result;
}

// Quote arguments using the Windows CommandLineToArgvW/CRT convention.
static std::wstring quote(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t c : value) {
        if (c == L'\\') { ++slashes; continue; }
        result.append(c == L'"' ? slashes * 2 + 1 : slashes, L'\\');
        slashes = 0;
        result += c;
    }
    result.append(slashes * 2, L'\\');
    return result + L"\"";
}

struct Snapshot {
    std::wstring folder, source;
    bool handed_off = false;
    ~Snapshot() {
        if (!handed_off && !folder.empty()) {
            DeleteFileW((folder + L"\\citation.input").c_str());
            DeleteFileW(source.c_str());
            DeleteFileW((folder + L"\\snapshot.owner").c_str());
            RemoveDirectoryW(folder.c_str());
        }
    }
    void create() {
        auto runtime = tool_directory + L"\\.runtime";
        auto base = runtime + L"\\snapshots";
        CreateDirectoryW(runtime.c_str(), nullptr);
        CreateDirectoryW(base.c_str(), nullptr);
        wchar_t unique[MAX_PATH] = {};
        if (!GetTempFileNameW(base.c_str(), L"epe", 0, unique))
            throw std::runtime_error("Cannot create snapshot in the tool folder.");
        DeleteFileW(unique);
        if (!CreateDirectoryW(unique, nullptr))
            throw std::runtime_error("Cannot create snapshot directory.");
        folder = unique;
        source = folder + L"\\scene.exo";
        HANDLE file = CreateFileW((folder + L"\\snapshot.owner").c_str(), GENERIC_WRITE,
            0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE)
            throw std::runtime_error("Cannot create snapshot marker.");
        const char marker[] = "EditablePptxExport snapshot v1\n";
        DWORD written = 0;
        BOOL ok = WriteFile(file, marker, sizeof(marker) - 1, &written, nullptr);
        CloseHandle(file);
        if (!ok || written != sizeof(marker) - 1)
            throw std::runtime_error("Cannot write snapshot marker.");
    }
};

typedef BOOL (__cdecl* SaveExo)(LPCSTR);
static SaveExo find_save_exo(FILTER* fp, SYS_INFO& info) {
    for (int i = 0; i < info.filter_n; ++i) {
        FILTER* exedit = static_cast<FILTER*>(fp->exfunc->get_filterp(i));
        if (!exedit || !exedit->information || !exedit->dll_hinst) continue;
        if (wide(exedit->information) !=
            L"\u62e1\u5f35\u7de8\u96c6(exedit) version 0.92 by \uff2b\uff25\uff2e\u304f\u3093") continue;
        auto base = reinterpret_cast<unsigned char*>(exedit->dll_hinst);
        auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
        if (dos->e_magic != IMAGE_DOS_SIGNATURE) break;
        auto nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE || nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386
            || nt->OptionalHeader.SizeOfImage != 0x261000) break;
        // ExEdit 0.92: global count of objects in the current scene.
        if (*reinterpret_cast<int*>(base + 0x15918C) <= 0)
            throw std::runtime_error("The current ExEdit scene has no objects.");
        MEMORY_BASIC_INFORMATION memory = {};
        auto address = base + 0x284D0;
        if (!VirtualQuery(address, &memory, sizeof(memory)) || memory.State != MEM_COMMIT
            || !(memory.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE
                                  | PAGE_EXECUTE_WRITECOPY))) break;
        return reinterpret_cast<SaveExo>(address);
    }
    throw std::runtime_error("Automatic capture requires ExEdit 0.92.\n"
                             "Other versions can use launch.cmd and a manually exported EXO.");
}

static std::string ansi_path(const std::wstring& path) {
    BOOL substituted = FALSE;
    int n = WideCharToMultiByte(932, WC_NO_BEST_FIT_CHARS, path.c_str(), -1,
        nullptr, 0, nullptr, &substituted);
    std::string result(n, 0);
    WideCharToMultiByte(932, WC_NO_BEST_FIT_CHARS, path.c_str(), -1,
        &result[0], n, nullptr, &substituted);
    if (substituted) throw std::runtime_error("Snapshot path cannot be represented in CP932.\n"
                                            "Move EditablePptxExport to a Japanese/ASCII path.");
    result.resize(n - 1);
    return result;
}

static bool locate_tool(HWND owner, FILTER* fp) {
    if (tool_directory.empty()) {
        wchar_t module_path[32768] = {};
        DWORD size = GetModuleFileNameW(fp->dll_hinst, module_path, 32768);
        if (size && size < 32768) {
            auto base = parent(module_path);
            if (is_file(base + L"\\gui.py")) tool_directory = base;
            else if (is_file(base + L"\\EditablePptxExport\\gui.py"))
                tool_directory = base + L"\\EditablePptxExport";
        }
    }
    if (tool_directory.empty()) {
        wchar_t selected[32768] = {};
        OPENFILENAMEW dialog = {};
        dialog.lStructSize = sizeof(dialog);
        dialog.hwndOwner = owner;
        dialog.lpstrFilter = L"Converter (gui.py)\0gui.py\0";
        dialog.lpstrFile = selected;
        dialog.nMaxFile = 32768;
        dialog.lpstrTitle = L"Select EditablePptxExport\\gui.py";
        dialog.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR;
        if (!GetOpenFileNameW(&dialog)) return false;
        tool_directory = parent(selected);
    }
    return true;
}

static void launch(HWND owner, FILTER* fp, void* editp) {
    SYS_INFO info = {};
    if (!editp || !fp->exfunc || !fp->exfunc->get_sys_info(editp, &info))
        throw std::runtime_error("Open an AviUtl project before exporting.");
    auto save_exo = find_save_exo(fp, info);
    // Capture the asset base before any file dialog, without changing the host cwd.
    wchar_t cwd[32768] = {};
    GetCurrentDirectoryW(32768, cwd);
    auto project = wide(info.project_name);
    auto source_base = project.empty() ? std::wstring(cwd) : parent(project);
    if (!locate_tool(owner, fp)) return;
    auto python = tool_directory + L"\\.venv\\Scripts\\pythonw.exe";
    auto gui = tool_directory + L"\\gui.py";
    if (!is_file(python) || !is_file(gui) || !is_file(tool_directory + L"\\exo_to_pptx.py")) {
        MessageBoxW(owner, L"Run setup.cmd in the EditablePptxExport folder first.\n"
                           L"Then select gui.py from that folder again.",
                    L"Editable PPTX Export", MB_OK | MB_ICONERROR);
        tool_directory.clear();
        return;
    }
    Snapshot snapshot;
    snapshot.create();
    // Export the whole current scene directly on AviUtl's UI thread. No scene,
    // selection, playback position, project filename, or objects are changed.
    auto encoded = ansi_path(snapshot.source);
    if (!save_exo(encoded.c_str()) || !is_file(snapshot.source))
        throw std::runtime_error("ExEdit could not export the current scene.");
    auto suggested = project.empty() ? source_base + L"\\AviUtl_editable.pptx"
                                    : project.substr(0, project.find_last_of(L'.')) + L"_editable.pptx";
    std::wstring command = quote(python) + L" -X utf8 " + quote(gui)
        + L" --source " + quote(snapshot.source) + L" --output " + quote(suggested)
        + L" --source-base " + quote(source_base) + L" --temporary-source";
    if (!project.empty()) {
        auto catalog = project.substr(0, project.find_last_of(L'.')) + L".references.json";
        if (is_file(catalog)) command += L" --default-references " + quote(catalog);
    }
    auto citation_file = project.empty() ? source_base + L"\\references.citations.json"
        : project.substr(0, project.find_last_of(L'.')) + L".citations.json";
    if (is_file(citation_file)) {
        auto exedit_base = reinterpret_cast<unsigned char*>(GetModuleHandleW(L"exedit.auf"));
        command += L" --citation-state " + quote(citation_file)
            + L" --citation-scene " + std::to_wstring(*reinterpret_cast<int*>(exedit_base + 0x1A5310));
    }
    std::vector<wchar_t> mutable_command(command.begin(), command.end());
    mutable_command.push_back(0);
    STARTUPINFOW startup = {};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {};
    if (!CreateProcessW(python.c_str(), mutable_command.data(), nullptr, nullptr, FALSE,
                        CREATE_NO_WINDOW, nullptr, tool_directory.c_str(), &startup, &process)) {
        const auto error = GetLastError();
        auto message = L"Could not start the converter. Windows error: " + std::to_wstring(error);
        MessageBoxW(owner, message.c_str(), L"Editable PPTX Export", MB_OK | MB_ICONERROR);
        return;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    snapshot.handed_off = true;
}


#ifdef EPE_CITATION
#include "citations_native.h"
#endif

BOOL func_WndProc(HWND hwnd, UINT message, WPARAM, LPARAM, void* editp, FILTER* fp) {
    // NO_CONFIG launchers use the host-owned export menu. They must not rely
    // on a configuration window receiving WM_FILTER_INIT to add a menu item.
#ifdef EPE_CITATION
    if (message == WM_FILTER_FILE_OPEN || message == WM_FILTER_FILE_CLOSE) ++citation_epoch;
#endif
    if (message == WM_FILTER_EXPORT && fp) {
        try {
#ifdef EPE_CITATION
            launch_citations(hwnd, fp, editp);
#else
            launch(hwnd, fp, editp);
#endif
        }
        catch (const std::exception& error) {
            auto message = wide(error.what());
            MessageBoxW(hwnd, message.c_str(), L"Editable PPTX Export", MB_OK | MB_ICONERROR);
        }
        catch (...) {
            MessageBoxW(hwnd, L"Could not open the converter.", L"Editable PPTX Export", MB_OK | MB_ICONERROR);
        }
    }
    return FALSE;  // no changes to the AviUtl image or timeline
}

extern "C" FILTER_DLL* __stdcall GetFilterTable() {
    static FILTER_DLL table = {};
    static bool initialized = false;
    if (!initialized) {
        // Convert the menu label to the ANSI encoding required by AviUtl 1.
        WideCharToMultiByte(932, 0,
#ifdef EPE_CITATION
            L"\u8ad6\u6587\u306e\u5f15\u7528\u30fb\u811a\u6ce8\u756a\u53f7\u3092\u66f4\u65b0",
#else
            L"\u73fe\u5728\u306e\u30b7\u30fc\u30f3\u304b\u3089\u7de8\u96c6\u53ef\u80fdPPTX\u3092\u4f5c\u6210",
#endif
            -1, menu_name, sizeof(menu_name), nullptr, nullptr);
        table.flag = FILTER_FLAG_EXPORT | FILTER_FLAG_ALWAYS_ACTIVE | FILTER_FLAG_NO_CONFIG
                   | FILTER_FLAG_PRIORITY_LOWEST | FILTER_FLAG_EX_INFORMATION;
        table.name = menu_name;
#ifdef EPE_CITATION
        table.information = const_cast<char*>("Editable PPTX Citation Manager 0.4.0 (ExEdit 0.92)");
#else
        table.information = const_cast<char*>("Editable PPTX Export 0.4.0 (ExEdit 0.92 scene capture)");
#endif
        table.func_WndProc = func_WndProc;
        initialized = true;
    }
    return &table;
}
