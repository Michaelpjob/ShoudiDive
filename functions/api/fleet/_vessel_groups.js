// Hand-kept inputs to the Find box's vessel groups (see _groups.js). Keys are
// MMSIs. Every entry here wins over the rules, so this is where a wrong guess
// gets fixed: add the hull to OVERRIDES with its group key and a short note.
//
// The research and government lists were built from the year of NOAA AIS in
// the local archive (June 2025 to June 2026, SoCal box): each MMSI is the one
// that hull actually broadcast, matched by name and call sign.

// MMSI -> who runs it
export const RESEARCH = {
  "369140000": "Scripps Institution of Oceanography",         // R/V Sally Ride, WSAF
  "303272000": "Scripps Institution of Oceanography",         // R/V Robert Gordon Sproul, WSQ2674
  "368244550": "Scripps Institution of Oceanography",         // R/V Bob and Betty Beyster, WDM8575
  "367800100": "academic research ship",                      // R/V Roger Revelle, KAOU
  "367241000": "Woods Hole Oceanographic Institution",        // R/V Atlantis, KAQP
  "369970573": "NOAA Fisheries",                              // NOAA Ship Reuben Lasker, WTEG
  "338924260": "NOAA Fisheries",                              // the Reuben Lasker's small boat
  "369970147": "NOAA Fisheries",                              // NOAA Ship Bell M. Shimada, WTED
  "366875820": "NOAA Channel Islands National Marine Sanctuary", // R/V Shearwater, WDB2424 (broadcasts type 30)
  "367139040": "NOAA national marine sanctuaries",            // R/V Fulmar, WDD3992
  "368420000": "Monterey Bay Aquarium Research Institute",    // R/V David Packard, WDN7986
  "368005670": "Monterey Bay Aquarium Research Institute",    // R/V Rachel Carson, WDJ7333
  "376404000": "Ocean Exploration Trust",                     // E/V Nautilus, J8B3605
  "367000160": "Southern California Marine Institute",        // R/V Yellowfin, WCW2778
  "338140286": "UC Santa Barbara Marine Science Institute",   // R/V Connell, WAM8729
  "338140283": "UC Santa Barbara Marine Science Institute",   // FISH ONE, same call sign
  "338140284": "UC Santa Barbara Marine Science Institute",   // FISH TWO, same call sign
  "338145223": "UC Santa Barbara Marine Science Institute",   // KELPFISH, same call sign
  "366997560": "Orange County Sanitation District ocean monitoring", // NERISSA, WDC2773
  "367659190": "City of San Diego ocean monitoring",          // MONITOR III, WDH8555
  "367659210": "City of San Diego ocean monitoring",          // OCEANUS, WDH8556
  "367666020": "City of Los Angeles ocean monitoring",        // M/V LA MER, WDH9236
};

// MMSI -> who runs it (the rules in _groups.js already catch most government
// hulls by name, call sign or MMSI block; these are the ones they miss)
export const GOVERNMENT = {
  "338010925": "National Park Service, Channel Islands",      // OCEAN RANGER
  "338004831": "National Park Service, Channel Islands",      // SEA RANGER II
};

// MMSI -> [group key, note]. Group keys: fleet, sport, fish, dive, research,
// gov, work, ship, private, unknown.
export const OVERRIDES = {
};
