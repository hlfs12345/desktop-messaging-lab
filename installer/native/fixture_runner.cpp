// Exclusivamente ensaios. Nao integra bundles ou instaladores do produto.
#include "installation.h"
#include <fstream>
int WINAPI wWinMain(HINSTANCE, HINSTANCE, wchar_t*, int) {
  auto directory = edisk_install::OwnDirectory();
  auto root = directory.parent_path().parent_path();
  edisk_install::Active active;
  edisk_install::Guard guard;
  if (!edisk_install::ReadActive(root, &active) ||
      !edisk_install::Runner(&guard, directory, "tecnico", active.version)) return 1;
  std::ifstream probe(root / L"test-runner-exit.txt");
  unsigned int exit_code = 0;
  if (probe >> exit_code) {
    std::ofstream(root / L"test-runner-started.txt").put('1');
    return static_cast<int>(exit_code);
  }
  Sleep(30000);
  return 0;
}
