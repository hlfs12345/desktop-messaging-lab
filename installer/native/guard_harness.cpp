#include "installation.h"
#include <iostream>
#include <string>
int wmain(int argc, wchar_t** argv) {
  if (argc < 3) return 2;
  edisk_install::Guard guard;
  if (!guard.Open(argv[2])) return 3;
  if (std::wstring(argv[1]) == L"hold") {
    std::cout << "LOCKED" << std::endl;
    std::string line;
    std::getline(std::cin, line);
    return 0;
  }
  edisk_install::Active active;
  return edisk_install::ReadActive(argv[2], &active) && edisk_install::Verify(argv[2], active) ? 0 : 4;
}
