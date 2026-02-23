#define _CRT_SECURE_NO_WARNINGS
// x86(32bit)環境で関数名が勝手に書き換わるのを防ぐ呪文
#pragma comment(linker, "/export:GetOutputPluginTable=_GetOutputPluginTable@0")

#include <windows.h>
#include <string>
#include <vector>
#include <stdio.h>
#include "output.h"    // AviUtl1用のヘッダ
#include "resource.h"  // GUIの設計図

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "stb_image_write.h"

// 設定値を保存するグローバル変数
int g_interval = 30;

// -------------------------------------------------------------------------
// INIファイルのパスを取得する関数（AviUtl1用のANSI文字列版）
// -------------------------------------------------------------------------
std::string GetIniFilePath() {
    char path[MAX_PATH];
    HMODULE hModule = NULL;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        (LPCSTR)&GetIniFilePath, &hModule);
    GetModuleFileNameA(hModule, path, MAX_PATH);

    std::string iniPath = path;
    size_t dot_pos = iniPath.find_last_of('.');
    if (dot_pos != std::string::npos) {
        iniPath = iniPath.substr(0, dot_pos) + ".ini";
    }
    return iniPath;
}

// -------------------------------------------------------------------------
// 設定画面（ダイアログ）の動作
// -------------------------------------------------------------------------
INT_PTR CALLBACK ConfigDlgProc(HWND hDlg, UINT message, WPARAM wParam, LPARAM lParam) {
    switch (message) {
    case WM_INITDIALOG:
        SetDlgItemInt(hDlg, IDC_EDIT1, g_interval, FALSE);
        return (INT_PTR)TRUE;

    case WM_COMMAND:
        if (LOWORD(wParam) == IDOK) {
            int val = GetDlgItemInt(hDlg, IDC_EDIT1, NULL, FALSE);
            if (val > 0) {
                g_interval = val;

                // INIファイルに保存（ANSI版）
                std::string iniPath = GetIniFilePath();
                std::string valStr = std::to_string(val);
                WritePrivateProfileStringA("Settings", "Interval", valStr.c_str(), iniPath.c_str());
            }
            EndDialog(hDlg, IDOK);
            return (INT_PTR)TRUE;
        }
        else if (LOWORD(wParam) == IDCANCEL) {
            EndDialog(hDlg, IDCANCEL);
            return (INT_PTR)TRUE;
        }
        break;
    }
    return (INT_PTR)FALSE;
}

// -------------------------------------------------------------------------
// 設定ボタンが押された時に呼ばれる関数（AviUtl 1仕様は「BOOL」）
// -------------------------------------------------------------------------
BOOL func_config(HWND hwnd, HINSTANCE dll_hinst) {
    DialogBox(dll_hinst, MAKEINTRESOURCE(IDD_DIALOG1), hwnd, ConfigDlgProc);
    return TRUE;
}

// -------------------------------------------------------------------------
// 出力処理の本体
// -------------------------------------------------------------------------
BOOL func_output(OUTPUT_INFO* oip) {
    // 処理開始前にINIから最新の値を読み込む
    std::string iniPath = GetIniFilePath();
    g_interval = GetPrivateProfileIntA("Settings", "Interval", 30, iniPath.c_str());
    if (g_interval <= 0) g_interval = 1;

    int interval = g_interval;
    int stride = (oip->w * 3 + 3) & ~3;

    if (oip->func_rest_time_disp) oip->func_rest_time_disp(0, oip->n);

    for (int i = 0; i < oip->n; i++) {
        if (oip->func_is_abort && oip->func_is_abort()) return FALSE;

        void* pixel_data = oip->func_get_video(i);

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

            char filename[MAX_PATH];
            strcpy(filename, oip->savefile);

            std::string base_name = filename;
            size_t dot_pos = base_name.find_last_of('.');
            if (dot_pos != std::string::npos) base_name = base_name.substr(0, dot_pos);

            char out_filename[MAX_PATH];
            sprintf_s(out_filename, "%s_%04d.png", base_name.c_str(), i);

            stbi_write_png(out_filename, oip->w, oip->h, 3, rgb_data.data(), oip->w * 3);
        }

        if (oip->func_rest_time_disp) oip->func_rest_time_disp(i, oip->n);
    }

    return TRUE;
}

// -------------------------------------------------------------------------
// テーブル定義
// -------------------------------------------------------------------------
OUTPUT_PLUGIN_TABLE output_plugin_table = {
    1,
    (LPSTR)"一定間隔PNG出力",
    (LPSTR)"PNG File (*.png)\0*.png\0",
    (LPSTR)"一定間隔PNG出力プラグイン v1.2",
    NULL,
    NULL,
    func_output,
    func_config, // ← 【重要】ここに func_config を登録！
    NULL,
    NULL
};

EXTERN_C __declspec(dllexport) OUTPUT_PLUGIN_TABLE* __stdcall GetOutputPluginTable(void) {
    return &output_plugin_table;
}