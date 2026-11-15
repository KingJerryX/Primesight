---
name: fire
description: What a fire or smoke briefing carries (people on and above the floor, how the building behaves, water, wind). Read this before writing any fire briefing.
---

# Fire briefing

Fact lines, in this order (4 at most):
1. PEOPLE: headcount on the fire floor and on the floors above it, from tenant records. Name a flagged tenant above
   (sedated patients, children, a lab) in the same line. Give the time-of-day caveat if the call is at night or a weekend.
2. BUILDING: stories, frame, sprinklers yes/no and the type if known. One line.
3. HYDRANTS: the two nearest, as H1 and H2 on the approach map, with metres and direction.
4. WIND: where it comes from, mph, and where smoke drifts (from outside-scout; the draft's line if that fails).
   If a tenant on or above the floor may store something hazardous, a HAZARD line replaces WIND.

NOT KNOWN: stair locations and standpipe and FDC locations, unless a crew note gives them.

Left for MORE: tenant names, the floor map line, roof, staging, exposures, the property manager, year built, the 3D tour.

Header: give the location as the caller said it (FLOOR 6, PARKING LEVEL 2). Never put notes about how the data was
looked up in the briefing.

Parking garage / EV fire: a parking level is not a tenant floor. Call gather_building with floor 1 unless the call says
the garage is below grade, so everyone in the building counts as above. A PARKING line (covered spaces, parking
elevators) from building.parking and elevators replaces HYDRANTS. Garage levels, clearance height and EV chargers are not in the data.

Don't add search or attack advice.
