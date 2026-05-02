import 'package:flutter/material.dart';
import 'home_screen.dart';
import 'theme/sid_tokens.dart';

void main() => runApp(const ShoudiDiveApp());

class ShoudiDiveApp extends StatelessWidget {
  const ShoudiDiveApp({super.key});

  @override
  Widget build(BuildContext context) {
    final base = ColorScheme.fromSeed(
      seedColor: SidColors.ink,
      brightness: Brightness.light,
    ).copyWith(
      surface: SidColors.bgPage,
      onSurface: SidColors.ink,
      primary: SidColors.ink,
      onPrimary: SidColors.card,
      secondary: SidColors.live,
      surfaceContainerHighest: SidColors.card,
      outlineVariant: SidColors.hairline,
    );

    return MaterialApp(
      title: 'ShoudiDive',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: base,
        scaffoldBackgroundColor: SidColors.bgPage,
        appBarTheme: const AppBarTheme(
          backgroundColor: SidColors.bgPage,
          foregroundColor: SidColors.ink,
          elevation: 0,
          surfaceTintColor: SidColors.bgPage,
          titleTextStyle: SidType.title,
        ),
        dividerColor: SidColors.hairline,
        chipTheme: ChipThemeData(
          backgroundColor: SidColors.card,
          selectedColor: SidColors.ink,
          labelStyle: SidType.chipLabel,
          secondaryLabelStyle: SidType.chipLabel.copyWith(color: SidColors.card),
          side: const BorderSide(color: SidColors.hairline),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(SidRadius.segmented),
          ),
          showCheckmark: false,
        ),
        iconTheme: const IconThemeData(color: SidColors.ink),
        textTheme: const TextTheme(
          headlineMedium: SidType.displayNum,
          headlineSmall: SidType.bigNum,
          titleMedium: SidType.title,
          bodyMedium: SidType.body,
          labelLarge: SidType.chipLabel,
          labelMedium: SidType.pill,
          labelSmall: SidType.eyebrow,
          bodySmall: SidType.caption,
        ),
      ),
      home: const HomeScreen(),
    );
  }
}
