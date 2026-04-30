class PlaceLabel {
  final String text;
  final double lng;
  final double lat;
  final double fontSize;
  final bool italic;
  final bool muted;
  final int priority;
  const PlaceLabel({
    required this.text,
    required this.lng,
    required this.lat,
    this.fontSize = 11,
    this.italic = false,
    this.muted = false,
    this.priority = 5,
  });
}

const List<PlaceLabel> kPlaceLabels = [
  PlaceLabel(text: 'MONTEREY BAY',     lng: -121.95, lat: 36.78, fontSize: 11, priority: 6),
  PlaceLabel(text: 'BIG SUR',          lng: -121.65, lat: 36.10, fontSize: 11, priority: 5),
  PlaceLabel(text: 'MORRO BAY',        lng: -120.82, lat: 35.36, fontSize: 11, priority: 5),
  PlaceLabel(text: 'PT. CONCEPTION',   lng: -120.42, lat: 34.46, fontSize: 11, priority: 6),
  PlaceLabel(text: 'SANTA BARBARA',    lng: -119.70, lat: 34.46, fontSize: 11, priority: 5),
  PlaceLabel(text: 'LOS ANGELES',      lng: -118.20, lat: 34.10, fontSize: 12, priority: 7),
  PlaceLabel(text: 'LA JOLLA',         lng: -117.20, lat: 32.86, fontSize: 11, priority: 5),
  PlaceLabel(text: 'SAN DIEGO',        lng: -117.10, lat: 32.65, fontSize: 11, priority: 6),
  PlaceLabel(text: 'TIJUANA',          lng: -116.95, lat: 32.50, fontSize: 11, priority: 5),
  PlaceLabel(text: 'LAS CORONADOS',    lng: -117.32, lat: 32.30, fontSize: 10, italic: true, muted: true, priority: 4),
  PlaceLabel(text: 'CHANNEL ISLANDS',  lng: -119.85, lat: 33.78, fontSize: 10, italic: true, muted: true, priority: 4),
  PlaceLabel(text: 'SOUTHERN CA BIGHT', lng: -118.95, lat: 33.20, fontSize: 11, italic: true, muted: true, priority: 3),
  PlaceLabel(text: 'PACIFIC OCEAN',    lng: -122.40, lat: 35.20, fontSize: 13, italic: true, muted: true, priority: 2),
];

class SavedSpot {
  final String id;
  final String name;
  final double lng;
  final double lat;
  const SavedSpot(this.id, this.name, this.lng, this.lat);
}

const List<SavedSpot> kSavedSpots = [
  SavedSpot('monterey',  'Monterey',       -121.92, 36.62),
  SavedSpot('morro',     'Morro Bay',      -120.88, 35.36),
  SavedSpot('pt-concep', 'Pt. Conception', -120.47, 34.45),
  SavedSpot('santabarb', 'Santa Barbara',  -119.70, 34.40),
  SavedSpot('santacruz', 'Santa Cruz I.',  -119.75, 34.05),
  SavedSpot('malibu',    'Malibu',         -118.78, 34.02),
  SavedSpot('catalina',  'Catalina',       -118.45, 33.39),
  SavedSpot('lajolla',   'La Jolla',       -117.28, 32.85),
  SavedSpot('sandiego',  'San Diego',      -117.18, 32.70),
  SavedSpot('coronados', 'Coronados',      -117.27, 32.40),
];
