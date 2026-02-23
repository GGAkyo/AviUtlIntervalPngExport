#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <string>
#include <vector>
#include <stdio.h>

#include "output2.h"
#include "resource.h"

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

// 設定値を保存するグローバル変数
int g_interval = 30;

// -------------------------------------------------------------------------
// 自分の.auo2と同じ場所にある .ini ファイルのパスを取得する関数（AviUtl2用ワイド文字対応）
// -------------------------------------------------------------------------
std::wstring GetIniFilePath() {
    wchar_t path[MAX_PATH];
    HMODULE hModule = NULL;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        (LPCWSTR)&GetIniFilePath, &hModule);
    GetModuleFileNameW(hModule, path, MAX_PATH);

    std::wstring iniPath = path;
    size_t dot_pos = iniPath.find_last_of(L'.');
    if (dot_pos != std::wstring::npos) {
        iniPath = iniPath.substr(0, dot_pos) + L".ini"; // 拡張子を .ini に変更
    }
    return iniPath;
}

// -------------------------------------------------------------------------
// 設定画面（ダイアログ）の動作を管理する関数
// -------------------------------------------------------------------------
INT_PTR CALLBACK ConfigDlgProc(HWND hDlg, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
    case WM_INITDIALOG:
        // 画面が開かれた時、現在の設定値を入力欄(IDC_EDIT1)に表示する
        SetDlgItemInt(hDlg, IDC_EDIT1, g_interval, FALSE);
        return (INT_PTR)TRUE;

    case WM_COMMAND:
        // ボタンが押された時の処理
        if (LOWORD(wParam) == IDOK) {
            // OKボタンが押されたら、入力欄の数字を読み取って変数に保存
            int val = GetDlgItemInt(hDlg, IDC_EDIT1, NULL, FALSE);
            if (val > 0) {
                g_interval = val;

                // ついでにINIファイルにも保存しておく（次回起動用）
                std::wstring iniPath = GetIniFilePath();
                std::wstring valStr = std::to_wstring(val);
                WritePrivateProfileStringW(L"Settings", L"Interval", valStr.c_str(), iniPath.c_str());
            }
            EndDialog(hDlg, IDOK); // 画面を閉じる
            return (INT_PTR)TRUE;
        }
        else if (LOWORD(wParam) == IDCANCEL) {
            // キャンセルボタンなら何もせず閉じる
            EndDialog(hDlg, IDCANCEL);
            return (INT_PTR)TRUE;
        }
        break;
    }
    return (INT_PTR)FALSE;
}

// -------------------------------------------------------------------------
// 設定ボタンが押された時に呼ばれる関数（GUIの起動）
// -------------------------------------------------------------------------
bool FuncConfig(HWND hwnd, HINSTANCE dll_hinst) {
    // さっき作ったダイアログ(IDD_DIALOG1)を表示する
    DialogBox(dll_hinst, MAKEINTRESOURCE(IDD_DIALOG1), hwnd, ConfigDlgProc);
    return TRUE;
}

// -------------------------------------------------------------------------
// 出力処理の本体
// -------------------------------------------------------------------------
bool OutputFunction(OUTPUT_INFO* oip) {
    // 処理開始前にINIから最新の値を読み込んでおく（念のため）
    std::wstring iniPath = GetIniFilePath();
    g_interval = GetPrivateProfileIntW(L"Settings", L"Interval", 30, iniPath.c_str());
    if (g_interval <= 0) g_interval = 1;

    // 変数 interval ではなく、グローバル変数 g_interval を使う
    int interval = g_interval;

    int stride = (oip->w * 3 + 3) & ~3;

    if (oip->func_rest_time_disp) oip->func_rest_time_disp(0, oip->n);

    for (int i = 0; i < oip->n; i++) {
        if (oip->func_is_abort && oip->func_is_abort()) return false;

        void* pixel_data = oip->func_get_video(i, 0);

        if (i % interval == 0 && pixel_data != nullptr) {
            std::vector<unsigned char> rgb_data(oip->w * oip->h * 3);
            unsigned char* src = (unsigned char*)pixel_data;

            for (int y = 0; y < oip->h; y++) {
                int src_y = oip->h - 1 - y;
                for (int x = 0; x < oip->w; x++) {
                    int src_idx = src_y * stride + x * 3;
                    int dst_idx = (y * oip->w + x) * 3;

                    rgb_data[dst_idx + 0] = src[src_idx + 2];
                    rgb_data[dst_idx + 1] = src[src_idx + 1];
                    rgb_data[dst_idx + 2] = src[src_idx + 0];
                }
            }

            // AviUtl2は保存先パスがワイド文字で来るので、stb_image_write用に一旦Shift-JISに戻す
            char filename_sjis[MAX_PATH];
            WideCharToMultiByte(CP_ACP, 0, oip->savefile, -1, filename_sjis, MAX_PATH, NULL, NULL);

            std::string base_name = filename_sjis;
            size_t dot_pos = base_name.find_last_of('.');
            if (dot_pos != std::string::npos) base_name = base_name.substr(0, dot_pos);

            char out_filename[MAX_PATH];
            sprintf_s(out_filename, "%s_%04d.png", base_name.c_str(), i);

            stbi_write_png(out_filename, oip->w, oip->h, 3, rgb_data.data(), oip->w * 3);
        }

        if (oip->func_rest_time_disp) oip->func_rest_time_disp(i, oip->n);
    }

    return true;
}

// -------------------------------------------------------------------------
// テーブル定義（修正）
// -------------------------------------------------------------------------
OUTPUT_PLUGIN_TABLE output_plugin_table = {
    OUTPUT_PLUGIN_TABLE::FLAG_VIDEO,
    L"一定間隔PNG出力",
    L"PNG File (*.png)\0*.png\0",
    L"一定間隔PNG出力プラグイン v1.2",
    OutputFunction,
    FuncConfig, // ←【重要】ここに FuncConfig を登録！以前は nullptr でした
    nullptr
};

extern "C" __declspec(dllexport) OUTPUT_PLUGIN_TABLE* GetOutputPluginTable(void) {
    return &output_plugin_table;
}