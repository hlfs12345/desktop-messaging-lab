#pragma once
#include <windows.h>
#include <array>
#include <cstring>
#include <string>
namespace edisk_install {
inline bool Variant(const std::wstring& executable, const std::string& channel, const std::string& version) {
  HMODULE module = LoadLibraryExW(executable.c_str(), nullptr, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE);
  if (!module) return false;
  HRSRC resource = FindResourceW(module, MAKEINTRESOURCEW(201), RT_RCDATA);
  bool valid = false;
  if (resource && SizeofResource(module, resource) == 32) {
    auto data = static_cast<const unsigned char*>(LockResource(LoadResource(module, resource)));
    if (data) {
      std::array<DWORD, 3> fields{};
      std::array<WORD, 6> words{};
      memcpy(fields.data(), data + 8, 12);
      memcpy(words.data(), data + 20, 12);
      std::string compiled;
      for (size_t i = 0; i < 4; ++i) compiled += (i ? "." : "") + std::to_string(words[i]);
      valid = memcmp(data, "EDSKVAR2", 8) == 0 && fields[0] == 2 &&
        fields[1] == (channel == "tecnico" ? 1u : 2u) &&
        fields[2] == (channel == "tecnico" ? 1u : 2u) &&
        words[4] == 0x8664 && words[5] == 3 && compiled == version;
    }
  }
  FreeLibrary(module);
  return valid;
}
}
