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
    id: "2026-08-12",
    date: "August 12, 2026",
    title: "Temperature breaks, outlined on the map",
    items: [
      {
        type: "new",
        text:
          "The water temperature layer now traces temperature breaks as " +
          "lines: the long edges where warm and cold water meet, like the " +
          "boundary of a warm push running between the islands toward the " +
          "coast. A break has to run at least 30 km end to end to draw, and " +
          "the strongest walls draw bold while softer edges stay thin, so "
          + "you see the fronts worth pointing a boat at, not local speckle. " +
          "These edges are where bait and pelagic fish tend to stack up, " +
          "and where water color often changes. The lines come straight " +
          "from the same satellite temperature data the map already shows, " +
          "nothing is added or smoothed in the temperature field itself. " +
          "Turn it off in Settings under Temp breaks. On days when " +
          "temperature falls back to a coarser backup satellite, the lines " +
          "hide themselves rather than draw from blurry data.",
      },
      {
        type: "new",
        text:
          "Tap any break line to get its coordinates: where it starts, " +
          "where it ends, and waypoints along the way, in both degrees " +
          "decimal minutes and decimal degrees. One button copies the " +
          "whole set, ready to paste into a GPS or chartplotter. A Breaks " +
          "button next to MPAs and Bottom turns the lines on and off.",
      },
    ],
  },
  {
    id: "2026-08-08",
    date: "August 8, 2026",
    title: "Water clarity is updating again",
    items: [
      {
        type: "fixed",
        text:
          "The water clarity layer had been frozen on July 4 data for over a " +
          "month. Our satellite provider renamed the dataset it comes from, so " +
          "every daily fetch quietly came back empty and the map kept showing " +
          "the last reading it had. It updates again. One honest note: the " +
          "provider retired the sharper version of this product, so clarity is " +
          "now a coarser grid than before, and it always runs about 11 days " +
          "behind because that is how long the science-quality processing takes.",
      },
      {
        type: "fixed",
        text:
          "A credential behind our automated data updates expired, which stopped " +
          "wind, temperature, and clarity from refreshing for about a day. " +
          "Everything is flowing again, and the layer age shown next to each " +
          "layer name was accurate the whole time, so conditions were never " +
          "presented as fresher than they were.",
      },
      {
        type: "improved",
        text:
          "Our own staleness alarm for the clarity layer was set tighter than " +
          "the data could ever be, so it cried wolf daily and made real outages " +
          "easy to miss. It now matches how often the source actually publishes.",
      },
    ],
  },
  {
    id: "2026-07-08",
    date: "July 8, 2026",
    title: "The phone layout works again",
    items: [
      {
        type: "fixed",
        text:
          "On phones, the top bar was overflowing the screen and hiding the " +
          "Settings gear, so units, theme, and layer opacity were unreachable. " +
          "It now fits every phone width, and the Kelp Paddy Finder link plus " +
          "the tip jar moved into the pull-up panel's new More section.",
      },
      {
        type: "fixed",
        text:
          "The forecast slider's readout was hiding behind the top bar on " +
          "phones. The selected time and value now show in a badge right " +
          "under the slider, and the slider itself is slimmer so you see " +
          "more map. Floating cards at the bottom of the map (marine " +
          "protected area notice, Navy closures, overlay buttons) stopped " +
          "stacking on top of each other, in portrait and landscape.",
      },
      {
        type: "new",
        text:
          "The bottom panel now answers gestures: swipe up on the strip to " +
          "open it, swipe down to close, or drag the open panel down and it " +
          "follows your finger.",
      },
      {
        type: "improved",
        text:
          "The layer chips at the bottom are readable again: data-age tags no " +
          "longer print on top of the layer names, and the current layer's " +
          "confidence (with its age, like “6d old”) now shows next to the layer " +
          "name in the status bar.",
      },
    ],
  },
  {
    id: "2026-06-23",
    date: "June 23, 2026",
    highlight: true,
    title: "Clearer about what the Paddy Finder knows",
    items: [
      {
        type: "improved",
        text:
          "The Kelp Paddy Finder now spells out exactly what you're looking at. " +
          "The green zones are labeled as model-estimated paddy likelihood, not " +
          "confirmed paddy sightings. The panel shows when the model was last built " +
          "so you can judge how fresh it is, and a quick reminder nudges you to " +
          "check weather, swell, fuel range, and closures before heading offshore.",
      },
    ],
  },
  {
    id: "2026-06-22",
    date: "June 22, 2026",
    highlight: true,
    title: "A smarter Kelp Paddy Finder",
    items: [
      {
        type: "improved",
        text:
          "The Paddy Finder's forecast is rebuilt on real satellite kelp-canopy " +
          "data for the whole Southern California Bight, from the Northern Channel " +
          "Islands down to the San Diego banks, so it shows where the kelp actually " +
          "is instead of a hand-drawn guess. Your community catch reports now " +
          "actively shape the map too, pulling the forecast toward the spots that " +
          "are producing, and paddies sitting over the named banks and breaks " +
          "score higher.",
      },
    ],
  },
  {
    id: "2026-06-21",
    date: "June 21, 2026",
    highlight: true,
    title: "Get your bearings on the Paddy Finder",
    items: [
      {
        type: "new",
        text:
          "The Kelp Paddy Finder now plots reference points right on the map — your " +
          "launch harbors, the named offshore banks (9-Mile, 14-Mile, Cortes, " +
          "Tanner…), and nearby dive spots — so you can orient at a glance and measure " +
          "a run against landmarks you know. They stay quiet at the overview; zoom into " +
          "an area and the names fade in. Toggle each set from the layers button.",
      },
      {
        type: "improved",
        text:
          "Catch reports now fade as they age, so the map leans on the freshest " +
          "paddies and stale intel quietly drops off.",
      },
    ],
  },
  {
    id: "2026-06-20",
    date: "June 20, 2026",
    highlight: true,
    title: "Report your catches on the Paddy Finder",
    items: [
      {
        type: "new",
        text:
          "Found a paddy holding fish? Log it on the Kelp Paddy Finder — tap the " +
          "spot, pick the species, and add it to the map for everyone. New reports " +
          "stay faint until they’re confirmed by multiple anglers, so the crowd " +
          "keeps the map honest.",
      },
    ],
  },
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
