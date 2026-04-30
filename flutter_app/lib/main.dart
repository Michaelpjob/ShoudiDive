import 'package:flutter/material.dart';
import 'home_screen.dart';

void main() => runApp(const ShoudiDiveApp());

class ShoudiDiveApp extends StatelessWidget {
  const ShoudiDiveApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'ShoudiDive',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF0A6E8C),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const HomeScreen(),
    );
  }
}
