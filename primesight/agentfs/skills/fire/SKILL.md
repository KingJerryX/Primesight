---
name: fire
description: What a fire or smoke briefing carries (who is on and above the floor, how the building behaves, how to get up). Read this before writing any fire briefing.
---

# Fire briefing

Fact lines, in this order (3 at most):
1. PEOPLE: who is on the fire floor (the tenants' names, up to 3 then "+n more", and the headcount) and the headcount
   on the floors above it, from
   tenant records. Name a flagged tenant above (sedated patients, children, a lab) in the same line. Give the
   time-of-day caveat if the call is at night or a weekend.
2. BUILDING: stories, frame, sprinklers yes/no and the type if known, and the elevators on record. One line.
3. HAZARD: only if a tenant on or above the floor may store something hazardous.

Hydrants, staging and wind are drawn on the APPROACH MAP: the text doesn't repeat them. The crew gets metres and
directions by replying MORE.

NOT KNOWN: stair locations and standpipe and FDC locations, unless a crew note gives them.

Left for MORE: hydrants and wind, other floors' tenant names, the floor map line, roof, staging, exposures, the property
manager, year built, the 3D tour.

Header: give the location as the caller said it (FLOOR 6, PARKING LEVEL 2). Never put notes about how the data was
looked up in the briefing.

Parking garage / EV fire: a parking level is not a tenant floor. Call gather_building with floor 1 unless the call says
the garage is below grade, so everyone in the building counts as above. A PARKING line (covered spaces, parking
elevators) from building.parking and elevators goes after PEOPLE. Garage levels, clearance height and EV chargers are not in the data.

Don't add search or attack advice.
