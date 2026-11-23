import 'package:flutter/material.dart';

ThemeData temaEdisk(Brightness brilho) {
  const primario = Color(0xFF2459A6);
  final claro = brilho == Brightness.light;
  final cores = ColorScheme.fromSeed(seedColor: primario, brightness: brilho)
      .copyWith(
        primary: claro ? primario : const Color(0xFF9CC4FF),
        onPrimary: claro ? Colors.white : const Color(0xFF102A48),
        surface: claro ? const Color(0xFFFFFFFF) : const Color(0xFF182230),
        onSurface: claro ? const Color(0xFF243247) : const Color(0xFFE2E8F0),
        error: claro ? const Color(0xFFC74747) : const Color(0xFFFFB4AB),
        outline: claro ? const Color(0xFFE2E8F0) : const Color(0xFF475569),
        surfaceContainerLowest: claro
            ? const Color(0xFFFFFFFF)
            : const Color(0xFF111827),
        surfaceContainerLow: claro
            ? const Color(0xFFF5F7FA)
            : const Color(0xFF1E293B),
      );
  final borda = OutlineInputBorder(
    borderRadius: BorderRadius.circular(10),
    borderSide: BorderSide(color: cores.outline),
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: cores,
    fontFamily: 'Segoe UI',
    scaffoldBackgroundColor: claro
        ? const Color(0xFFF5F7FA)
        : const Color(0xFF111827),
    dividerColor: cores.outline,
    appBarTheme: AppBarTheme(
      backgroundColor: cores.surface,
      foregroundColor: cores.onSurface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(
        color: cores.onSurface,
        fontSize: 22,
        fontWeight: FontWeight.w700,
      ),
      shape: Border(bottom: BorderSide(color: cores.outline)),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: const Color(0xFF183153),
      indicatorColor: const Color(0xFF2459A6),
      indicatorShape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(10),
      ),
      selectedIconTheme: const IconThemeData(color: Colors.white),
      unselectedIconTheme: const IconThemeData(color: Color(0xFFCBD5E1)),
      selectedLabelTextStyle: const TextStyle(
        color: Colors.white,
        fontWeight: FontWeight.w600,
      ),
      unselectedLabelTextStyle: const TextStyle(color: Color(0xFFCBD5E1)),
    ),
    cardTheme: CardThemeData(
      color: cores.surface,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: cores.outline),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: cores.surface,
      border: borda,
      enabledBorder: borda,
      focusedBorder: borda.copyWith(
        borderSide: BorderSide(color: cores.primary, width: 2),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        minimumSize: const Size(0, 44),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(0, 44),
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        minimumSize: const Size(0, 40),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
      ),
    ),
    listTileTheme: ListTileThemeData(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 5),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      iconColor: cores.primary,
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: cores.surface,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    ),
    chipTheme: ChipThemeData(
      side: BorderSide(color: cores.outline),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
    ),
    textTheme: const TextTheme(
      headlineLarge: TextStyle(
        fontSize: 32,
        height: 1.2,
        fontWeight: FontWeight.w700,
      ),
      headlineSmall: TextStyle(
        fontSize: 24,
        height: 1.25,
        fontWeight: FontWeight.w700,
      ),
      titleLarge: TextStyle(
        fontSize: 20,
        height: 1.3,
        fontWeight: FontWeight.w600,
      ),
      titleMedium: TextStyle(
        fontSize: 16,
        height: 1.4,
        fontWeight: FontWeight.w600,
      ),
      bodyLarge: TextStyle(fontSize: 16, height: 1.5),
      bodyMedium: TextStyle(fontSize: 14, height: 1.5),
    ),
  );
}
