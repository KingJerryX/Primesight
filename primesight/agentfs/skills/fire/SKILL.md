---
name: fire
description: What a fire or smoke briefing leads with (people above, water supply, stairs and standpipes, roof, staging). Read this before writing any fire briefing.
---

# Fire briefing

Order of lines, after the header:
1. ABOVE FIRE: people on floors above the fire floor (from tenant headcounts), and any flagged tenant above (sedated patients, children, labs).
2. ON FLOOR: who is on the fire floor, suites, headcount.
3. WATER and WIND from outside-scout on one line: hydrants within 300 m with the two nearest (metres, direction), and wind
   from / mph / where smoke drifts (live Open-Meteo reading). Fall back to the HYDRANTS line in the draft if outside-scout fails.
4. INSIDE: the scan line, and the EXIT SIGNS line from the scan if there is one. Say "N located (seen from X of Y scan points)": each located sign is counted once; it is not a stair location.
5. Building: stories, year built, frame, sprinklers yes/no and type if known.
6. ROOF and OPEN LOT for staging (aerial search, verify). NEXT TO from outside-scout if it has one (exposures:
   neighbouring building, distance, stories, sprinklers; kit records only), noting one that lies where smoke drifts.
7. PROPERTY MGR contact if known.
8. NOT KNOWN: always include stair locations and standpipe locations unless a crew note gives them.

Header: give the location as the caller said it (FLOOR 6, PARKING LEVEL 2). Never put notes about how the data was
looked up in the briefing.

Parking garage / EV fire: a parking level is not a tenant floor. Call gather_building with floor 1 unless the call says
the garage is below grade, so everyone in the building counts as above. Give the PARKING line (covered spaces, parking
elevators) from building.parking and elevators. Garage levels, clearance height and EV chargers are not in the data.

Don't add search or attack advice.
