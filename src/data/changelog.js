// User-facing "What's New" — curated + benefit-framed.
//
// This is NOT the developer changelog (that's git history / PRs). Add an entry
// here only when a change is meaningful to a diver, and write it as the
// benefit, not the implementation ("water clarity is more reliable", not
// "fixed OB.DAAC file_search 422"). We ship many PRs/day; most never belong
// here.
//
// Shape:
//   { id, date, title, highlight?, items: [{ type: "new"|"improved"|"fixed", text }] }
//   - id: sortable string (YYYY-MM-DD[-n]); the newest entry's id drives the
//         unread dot (compared against localStorage "sd:whatsnew:seen").
//   - highlight: true → eligible for a one-time first-visit nudge (optional).
// Keep newest first.

export const CHANGELOG = [
  {
    id: "2026-06-19-2",
    date: "June 19, 2026",
    highlight: true,
    title: "New tool: Kelp Paddy Finder (beta)",
    items: [
      {
        type: "new",
        text:
          "A new beta tool maps where drifting kelp paddies — the floating rafts " +
          "that hold yellowtail, dorado, and tuna — are most likely concentrating " +
          "offshore in Southern California right now. Open it from the “🪸 Paddy " +
          "Finder” link at the top.",
      },
      {
        type: "new",
        text:
          "Scrub a time slider from the last 3 days through a 2-day forecast to see " +
          "how the paddies have drifted and where they’re heading. Drag a ruler from " +
          "your launch to measure the run in nautical miles, and tap anywhere to drop " +
          "a waypoint and copy its GPS coordinates.",
      },
    ],
  },
  {
    id: "2026-06-19",
    date: "June 19, 2026",
    highlight: true,
    title: "San Clemente Island closures + more reliable water clarity",
    items: [
      {
        type: "new",
        text:
          "Navy closures around San Clemente Island. Turn on the “Navy” " +
          "layer to see which areas are closed today and across the next 7 days — " +
          "each zone shows its GPS coordinates and exact closure times. Tap a zone " +
          "for details before you plan a trip out there.",
      },
      {
        type: "improved",
        text:
          "Water-clarity (chlorophyll) data is noticeably more reliable. It now " +
          "blends multiple independent satellite providers — NASA, NOAA, and the " +
          "EU’s Copernicus Marine — so the map fills in more completely and " +
          "rarely goes stale, even when one provider has an outage.",
      },
      {
        type: "improved",
        text:
          "Confidence badges now tell you when a layer is running on a backup data " +
          "source, so you always know how much to trust what you’re looking at.",
      },
    ],
  },
  {
    id: "2026-06-10",
    date: "June 2026",
    title: "See into the water column + kelp beds",
    items: [
      {
        type: "new",
        text:
          "Depth-resolved visibility (beta): the visibility layer now estimates how " +
          "clarity changes with depth, not just at the surface — useful for judging " +
          "a dive before you splash.",
      },
      {
        type: "new",
        text:
          "Kelp-bed overlay: current kelp canopy from Landsat, so you can find " +
          "structure and plan entries around the forest.",
      },
    ],
  },
  {
    id: "2026-05-20",
    date: "May 2026",
    title: "Surface currents + Baja Mexico",
    items: [
      {
        type: "new",
        text:
          "Surface-current layer (beta): drift direction and speed from HF-radar " +
          "plus tide and wind — the read that matters most for safety and anchoring.",
      },
      {
        type: "new",
        text:
          "Baja Mexico (beta): Pacific coast and Sea of Cortez, Ensenada to Cabo and " +
          "La Paz. Switch regions from the top bar.",
      },
      {
        type: "new",
        text:
          "Spot Detail view: tap a saved spot for close-up bathymetry, depth contours, " +
          "and the conditions clipped to that spot.",
      },
    ],
  },
];

// Newest entry id — drives the unread indicator.
export const LATEST_CHANGELOG_ID = CHANGELOG.length ? CHANGELOG[0].id : "";
